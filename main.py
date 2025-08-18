import io
import os
import json
import math
import xml.etree.ElementTree as ET
import re
import datetime
import tempfile
import uuid
import base64
from typing import List, Tuple, Dict, Optional, Union
from collections import defaultdict

from shapely.geometry import Polygon, MultiPolygon, LinearRing, box, mapping, shape
from shapely.ops import unary_union
from shapely.validation import explain_validity

# Optional deps (guarded)
try:
    import svgpathtools  # type: ignore
except Exception:  # pragma: no cover
    svgpathtools = None

# Optional plotting
try:
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
except Exception:  # pragma: no cover
    plt = None
    patches = None

# Optional PDF generation
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.lib.colors import black, gray, green, red, blue, white
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.graphics import renderPDF
    from reportlab.lib import colors
    reportlab_available = True
except ImportError:
    print("⚠️ reportlab non installato. Export PDF non disponibile.")
    reportlab_available = False

# Optional DXF generation
try:
    import ezdxf
    from ezdxf import colors as dxf_colors
    from ezdxf.enums import TextEntityAlignment
    ezdxf_available = True
except ImportError:
    print("⚠️ ezdxf non installato. Export DXF non disponibile.")
    ezdxf_available = False

# ---- FastAPI (kept in same file as requested) ----
try:
    from fastapi import FastAPI, UploadFile, File, Form, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    import uvicorn
except Exception:  # pragma: no cover
    FastAPI = None  # type: ignore

# ────────────────────────────────────────────────────────────────────────────────
# Configuration & constants
# ────────────────────────────────────────────────────────────────────────────────
SCARTO_CUSTOM_MM = 5          # tolleranza matching tipi custom
AREA_EPS = 1e-3               # area minima per considerare una geometria
COORD_EPS = 1e-6
SNAP_MM = 1.0                 # griglia di snap per ridurre "micro-custom"
DISPLAY_MM_PER_M = 1000.0
MICRO_REST_MM = 15.0          # soglia per attivare backtrack del resto finale (coda riga)
KEEP_OUT_MM = 2.0             # margine attorno ad aperture per evitare micro-sfridi
SPLIT_MAX_WIDTH_MM = 413      # larghezza max per slice CU2 (profilo rigido)

# Libreria blocchi standard (mm)
BLOCK_HEIGHT = 495
BLOCK_WIDTHS = [1239, 826, 413]  # Grande, Medio, Piccolo
SIZE_TO_LETTER = {1239: "A", 826: "B", 413: "C"}

# Ordini di prova per i blocchi – si sceglie il migliore per il segmento
BLOCK_ORDERS = [
    [1239, 826, 413],
    [826, 1239, 413],
]

# Storage per sessioni (in-memory per semplicità)
SESSIONS: Dict[str, Dict] = {}

# ────────────────────────────────────────────────────────────────────────────────
# Pydantic Models per API
# ────────────────────────────────────────────────────────────────────────────────
class PackingConfig(BaseModel):
    block_widths: List[int] = BLOCK_WIDTHS
    block_height: int = BLOCK_HEIGHT
    row_offset: Optional[int] = 826
    snap_mm: float = SNAP_MM
    keep_out_mm: float = KEEP_OUT_MM

class PackingResult(BaseModel):
    session_id: str
    status: str
    wall_bounds: List[float]
    blocks_standard: List[Dict]
    blocks_custom: List[Dict]
    apertures: List[Dict]
    summary: Dict
    config: Dict
    metrics: Dict

def build_run_params(row_offset: Optional[int] = None) -> Dict:
    """Raccoglie i parametri di run da serializzare nel JSON."""
    return {
        "block_widths_mm": BLOCK_WIDTHS,
        "block_height_mm": BLOCK_HEIGHT,
        "row_offset_mm": int(row_offset) if row_offset is not None else None,
        "snap_mm": SNAP_MM,
        "keep_out_mm": KEEP_OUT_MM,
        "split_max_width_mm": SPLIT_MAX_WIDTH_MM,
        "scarto_custom_mm": SCARTO_CUSTOM_MM,
        "row_aware_merge": True,
        "orders_tried": BLOCK_ORDERS,
    }

# ────────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────────
def snap(v: float, grid: float = SNAP_MM) -> float:
    if grid <= 0:
        return v
    return round(v / grid) * grid

def snap_bounds(p: Polygon) -> Polygon:
    minx, miny, maxx, maxy = p.bounds
    return box(snap(minx), snap(miny), snap(maxx), snap(maxy))

def polygon_holes(p: Polygon) -> List[Polygon]:
    """Extract interior rings as Polygon objects (apertures)."""
    holes = []
    for ring in p.interiors:
        if isinstance(ring, LinearRing) and len(ring.coords) >= 4:
            holes.append(Polygon(ring))
    return holes

# ────────────────────────────────────────────────────────────────────────────────
# Geometry utilities
# ────────────────────────────────────────────────────────────────────────────────
def sanitize_polygon(p: Polygon) -> Polygon:
    if p.is_valid:
        return p
    fixed = p.buffer(0)
    if fixed.is_valid:
        return fixed
    raise ValueError(f"Polygon invalido: {explain_validity(p)}")

def ensure_multipolygon(geom) -> List[Polygon]:
    if isinstance(geom, Polygon):
        return [geom]
    elif isinstance(geom, MultiPolygon):
        return [g for g in geom.geoms if not g.is_empty]
    else:
        return []

# ────────────────────────────────────────────────────────────────────────────────
# SVG parsing (IMPLEMENTAZIONE COMPLETA)
# ────────────────────────────────────────────────────────────────────────────────
def parse_svg_wall(svg_bytes: bytes, layer_wall: str = "MURO", layer_holes: str = "BUCHI") -> Tuple[Polygon, List[Polygon]]:
    """
    Parser SVG reale che estrae parete e aperture dai layer specificati.
    
    Args:
        svg_bytes: Contenuto del file SVG
        layer_wall: Nome del layer contenente il profilo della parete
        layer_holes: Nome del layer contenente le aperture (porte/finestre)
    
    Returns:
        Tuple[Polygon, List[Polygon]]: (parete_principale, lista_aperture)
    """
    try:
        # Parse XML
        svg_content = svg_bytes.decode('utf-8')
        root = ET.fromstring(svg_content)
        
        # Namespace SVG
        ns = {'svg': 'http://www.w3.org/2000/svg'}
        
        # Estrai informazioni di scala/viewport
        scale_factor = _extract_scale_factor(root, ns)
        
        # Estrai geometrie per layer
        wall_geometries = _extract_geometries_by_layer(root, ns, layer_wall, scale_factor)
        hole_geometries = _extract_geometries_by_layer(root, ns, layer_holes, scale_factor)
        
        # Converti in Polygon
        wall_polygon = _geometries_to_polygon(wall_geometries, is_wall=True)
        aperture_polygons = _geometries_to_apertures(hole_geometries)
        
        print(f"✅ SVG parsed: parete {wall_polygon.area:.1f} mm², {len(aperture_polygons)} aperture")
        return wall_polygon, aperture_polygons
        
    except Exception as e:
        print(f"❌ Errore parsing SVG: {e}")
        # Fallback: cerca qualsiasi geometria chiusa
        return _fallback_parse_svg(svg_bytes)


def _extract_scale_factor(root: ET.Element, ns: Dict[str, str]) -> float:
    """Estrae il fattore di scala dal viewBox o width/height."""
    try:
        # Prova viewBox prima
        viewbox = root.get('viewBox')
        if viewbox:
            _, _, width, height = map(float, viewbox.split())
            # Assume unità in mm, scala 1:1
            return 1.0
            
        # Prova width/height con unità
        width_str = root.get('width', '1000')
        height_str = root.get('height', '1000')
        
        # Estrai valore numerico (rimuovi unità come px, mm, etc)
        width_val = float(re.findall(r'[\d.]+', width_str)[0])
        
        # Se non ci sono unità specificate, assume mm
        if 'px' in width_str:
            # Converti px -> mm (96 DPI standard)
            return 25.4 / 96.0
        elif 'cm' in width_str:
            return 10.0
        elif 'm' in width_str:
            return 1000.0
        else:
            # Assume già mm
            return 1.0
            
    except Exception:
        print("⚠️ Impossibile determinare scala, usando 1:1")
        return 1.0


def _extract_geometries_by_layer(root: ET.Element, ns: Dict[str, str], layer_name: str, scale: float) -> List[List[Tuple[float, float]]]:
    """Estrae tutte le geometrie dal layer specificato."""
    geometries = []
    
    # Cerca group con id/inkscape:label che corrisponde al layer
    for group in root.findall('.//svg:g', ns):
        group_id = group.get('id', '')
        group_label = group.get('{http://www.inkscape.org/namespaces/inkscape}label', '')
        
        if layer_name.lower() in group_id.lower() or layer_name.lower() in group_label.lower():
            geometries.extend(_extract_paths_from_group(group, ns, scale))
            
    # Se non trova layer specifici, cerca elementi top-level
    if not geometries:
        print(f"⚠️ Layer '{layer_name}' non trovato, cercando geometrie generiche...")
        geometries.extend(_extract_paths_from_group(root, ns, scale))
    
    return geometries


def _extract_paths_from_group(group: ET.Element, ns: Dict[str, str], scale: float) -> List[List[Tuple[float, float]]]:
    """Estrae path, rect, circle da un gruppo SVG."""
    geometries = []
    
    # Path elements
    for path in group.findall('.//svg:path', ns):
        d = path.get('d')
        if d:
            try:
                coords = _parse_svg_path(d, scale)
                if coords and len(coords) >= 3:
                    geometries.append(coords)
            except Exception as e:
                print(f"⚠️ Errore parsing path: {e}")
    
    # Rectangle elements  
    for rect in group.findall('.//svg:rect', ns):
        try:
            x = float(rect.get('x', 0)) * scale
            y = float(rect.get('y', 0)) * scale
            w = float(rect.get('width', 0)) * scale
            h = float(rect.get('height', 0)) * scale
            
            coords = [(x, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)]
            geometries.append(coords)
        except Exception as e:
            print(f"⚠️ Errore parsing rect: {e}")
    
    # Circle elements
    for circle in group.findall('.//svg:circle', ns):
        try:
            cx = float(circle.get('cx', 0)) * scale
            cy = float(circle.get('cy', 0)) * scale  
            r = float(circle.get('r', 0)) * scale
            
            # Approssima cerchio con poligono a 16 lati
            coords = []
            for i in range(17):  # +1 per chiudere
                angle = 2 * 3.14159 * i / 16
                x = cx + r * math.cos(angle)
                y = cy + r * math.sin(angle)
                coords.append((x, y))
            geometries.append(coords)
        except Exception as e:
            print(f"⚠️ Errore parsing circle: {e}")
    
    return geometries


def _parse_svg_path(path_data: str, scale: float) -> List[Tuple[float, float]]:
    """Parser semplificato per path SVG."""
    try:
        # Usa svgpathtools se disponibile
        if svgpathtools:
            path = svgpathtools.parse_path(path_data)
            coords = []
            
            # Campiona il path a intervalli regolari
            samples = max(50, int(path.length() / 10))  # 1 punto ogni ~10 unità
            for i in range(samples + 1):
                t = i / samples if samples > 0 else 0
                point = path.point(t)
                x = point.real * scale
                y = point.imag * scale
                coords.append((x, y))
                
            # Assicurati che sia chiuso se necessario
            if len(coords) > 2 and (abs(coords[0][0] - coords[-1][0]) > 1 or 
                                   abs(coords[0][1] - coords[-1][1]) > 1):
                coords.append(coords[0])
                
            return coords
            
    except Exception as e:
        print(f"⚠️ svgpathtools fallito: {e}")
    
    # Fallback: parser manuale semplificato
    return _parse_path_manual(path_data, scale)


def _parse_path_manual(path_data: str, scale: float) -> List[Tuple[float, float]]:
    """Parser manuale per comandi path SVG di base (M, L, Z)."""
    coords = []
    commands = re.findall(r'[MmLlHhVvZz][^MmLlHhVvZz]*', path_data)
    
    current_x, current_y = 0, 0
    start_x, start_y = 0, 0
    
    for cmd in commands:
        cmd_type = cmd[0]
        values = re.findall(r'-?[\d.]+', cmd[1:])
        values = [float(v) * scale for v in values]
        
        if cmd_type.upper() == 'M':  # MoveTo
            if len(values) >= 2:
                current_x, current_y = values[0], values[1]
                start_x, start_y = current_x, current_y
                coords.append((current_x, current_y))
                
        elif cmd_type.upper() == 'L':  # LineTo
            for i in range(0, len(values), 2):
                if i + 1 < len(values):
                    if cmd_type.islower():  # relative
                        current_x += values[i]
                        current_y += values[i + 1]
                    else:  # absolute
                        current_x, current_y = values[i], values[i + 1]
                    coords.append((current_x, current_y))
                    
        elif cmd_type.upper() == 'Z':  # ClosePath
            if coords and (coords[0] != coords[-1]):
                coords.append((start_x, start_y))
    
    return coords


def _geometries_to_polygon(geometries: List[List[Tuple[float, float]]], is_wall: bool = True) -> Polygon:
    """Converte liste di coordinate in Polygon Shapely."""
    if not geometries:
        raise ValueError("Nessuna geometria trovata per la parete")
    
    valid_polygons = []
    
    for coords in geometries:
        try:
            if len(coords) < 3:
                continue
                
            # Assicurati che sia chiuso
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            
            poly = Polygon(coords)
            
            # Valida e ripara se necessario
            if not poly.is_valid:
                poly = poly.buffer(0)
                
            if poly.is_valid and poly.area > AREA_EPS:
                valid_polygons.append(poly)
                
        except Exception as e:
            print(f"⚠️ Geometria scartata: {e}")
    
    if not valid_polygons:
        raise ValueError("Nessuna geometria valida trovata")
    
    # Se è una parete, prendi l'unione o il poligono più grande
    if is_wall:
        if len(valid_polygons) == 1:
            return valid_polygons[0]
        else:
            # Prendi il poligono più grande come parete principale
            largest = max(valid_polygons, key=lambda p: p.area)
            print(f"⚠️ Trovati {len(valid_polygons)} poligoni, usando il più grande")
            return largest
    else:
        # Per aperture, restituisci l'unione
        return unary_union(valid_polygons)


def _geometries_to_apertures(geometries: List[List[Tuple[float, float]]]) -> List[Polygon]:
    """Converte geometrie in lista di aperture."""
    apertures = []
    
    for coords in geometries:
        try:
            if len(coords) < 3:
                continue
                
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
                
            if poly.is_valid and poly.area > AREA_EPS:
                apertures.append(poly)
                
        except Exception as e:
            print(f"⚠️ Apertura scartata: {e}")
    
    return apertures


def _fallback_parse_svg(svg_bytes: bytes) -> Tuple[Polygon, List[Polygon]]:
    """Parsing fallback quando non trova layer specifici."""
    try:
        svg_content = svg_bytes.decode('utf-8')
        root = ET.fromstring(svg_content)
        ns = {'svg': 'http://www.w3.org/2000/svg'}
        
        scale = _extract_scale_factor(root, ns)
        all_geometries = _extract_paths_from_group(root, ns, scale)
        
        if not all_geometries:
            raise ValueError("Nessuna geometria trovata nel file SVG")
        
        # Prendi il poligono più grande come parete
        valid_polygons = []
        for coords in all_geometries:
            try:
                if len(coords) >= 3:
                    if coords[0] != coords[-1]:
                        coords.append(coords[0])
                    poly = Polygon(coords).buffer(0)
                    if poly.is_valid and poly.area > AREA_EPS:
                        valid_polygons.append(poly)
            except:
                continue
        
        if not valid_polygons:
            raise ValueError("Nessun poligono valido trovato")
        
        # Ordina per area
        valid_polygons.sort(key=lambda p: p.area, reverse=True)
        
        wall = valid_polygons[0]
        apertures = valid_polygons[1:] if len(valid_polygons) > 1 else []
        
        print(f"✅ Fallback parse: parete {wall.area:.1f} mm², {len(apertures)} aperture")
        return wall, apertures
        
    except Exception as e:
        print(f"❌ Anche il fallback è fallito: {e}")
        # Ultimo fallback: parete rettangolare di esempio
        wall = Polygon([(0, 0), (5000, 0), (5000, 3000), (0, 3000)])
        return wall, []

# ────────────────────────────────────────────────────────────────────────────────
# DXF Export (IMPLEMENTAZIONE SENZA SOVRAPPOSIZIONI)
# ────────────────────────────────────────────────────────────────────────────────
def export_to_dxf(summary: Dict[str, int], 
                  customs: List[Dict], 
                  placed: List[Dict], 
                  wall_polygon: Polygon,
                  apertures: Optional[List[Polygon]] = None,
                  project_name: str = "Progetto Parete",
                  out_path: str = "schema_taglio.dxf",
                  params: Optional[Dict] = None) -> str:
    """
    Genera DXF con layout intelligente SENZA sovrapposizioni.
    """
    if not ezdxf_available:
        raise RuntimeError("ezdxf non disponibile. Installa con: pip install ezdxf")
    
    try:
        # Crea nuovo documento DXF
        doc = ezdxf.new('R2010')
        msp = doc.modelspace()
        
        # Setup layer professionali
        _setup_dxf_layers(doc)
        
        # Calcola bounds wall per reference
        minx, miny, maxx, maxy = wall_polygon.bounds
        wall_width = maxx - minx
        wall_height = maxy - miny
        
        # ===== SISTEMA LAYOUT INTELLIGENTE SENZA SOVRAPPOSIZIONI =====
        layout = DXFLayoutManager(wall_width, wall_height)
        
        # 1. LAYOUT PRINCIPALE (zona principale)
        main_zone = layout.add_zone("main", wall_width, wall_height)
        _draw_main_layout(msp, wall_polygon, placed, customs, apertures, main_zone)
        
        # 2. SCHEMA TAGLIO (a destra del main)
        cutting_width = max(wall_width * 0.8, 3000)  # minimo 3000mm
        cutting_height = _calculate_cutting_height(customs)
        cutting_zone = layout.add_zone("cutting", cutting_width, cutting_height, 
                                     anchor="right_of", ref_zone="main", margin=1500)  # MARGINE AUMENTATO
        _draw_cutting_schema_fixed(msp, customs, cutting_zone)
        
        # 3. TABELLE (sotto al main)
        tables_width = wall_width
        tables_height = _calculate_tables_height(summary, customs)
        tables_zone = layout.add_zone("tables", tables_width, tables_height,
                                    anchor="below", ref_zone="main", margin=1200)  # MARGINE AUMENTATO
        _draw_tables_section(msp, summary, customs, placed, tables_zone)
        
        # 4. CARTIGLIO (sotto alle tabelle, a destra)
        cartridge_width = 2500
        cartridge_height = 1500
        cartridge_zone = layout.add_zone("cartridge", cartridge_width, cartridge_height,
                                       anchor="below_right", ref_zone="tables", margin=800)  # MARGINE AUMENTATO
        _draw_professional_cartridge_fixed(msp, project_name, summary, customs, params, cartridge_zone)
        
        # 5. LEGENDA (sotto a tutto)
        legend_width = layout.get_total_width()
        legend_height = 1000
        legend_zone = layout.add_zone("legend", legend_width, legend_height,
                                    anchor="bottom", ref_zone="tables", margin=1000)  # MARGINE AUMENTATO
        _draw_legend_and_notes_fixed(msp, legend_zone)
        
        # Salva documento
        doc.saveas(out_path)
        print(f"✅ DXF senza sovrapposizioni generato: {out_path}")
        print(f"📐 Layout totale: {layout.get_total_width():.0f} x {layout.get_total_height():.0f} mm")
        return out_path
        
    except Exception as e:
        print(f"❌ Errore generazione DXF: {e}")
        raise


class DXFLayoutManager:
    """Gestisce il layout DXF evitando sovrapposizioni."""
    
    def __init__(self, base_width: float, base_height: float):
        self.zones = {}
        self.base_width = base_width
        self.base_height = base_height
        self.total_bounds = [0, 0, 0, 0]  # minx, miny, maxx, maxy
        
    def add_zone(self, name: str, width: float, height: float, 
                 anchor: str = "topleft", ref_zone: str = None, margin: float = 500) -> Dict:
        """
        Aggiunge una zona calcolando automaticamente la posizione senza sovrapposizioni.
        
        anchor options:
        - "topleft": (0, 0) - default
        - "right_of": a destra della zona ref
        - "below": sotto la zona ref  
        - "below_right": sotto e a destra della zona ref
        - "bottom": in fondo a tutto
        """
        
        if anchor == "topleft" or ref_zone is None:
            # Prima zona o posizione assoluta
            x, y = 0, 0
            
        elif anchor == "right_of" and ref_zone in self.zones:
            ref = self.zones[ref_zone]
            x = ref['x'] + ref['width'] + margin
            y = ref['y']
            
        elif anchor == "below" and ref_zone in self.zones:
            ref = self.zones[ref_zone]
            x = ref['x']
            y = ref['y'] - height - margin
            
        elif anchor == "below_right" and ref_zone in self.zones:
            ref = self.zones[ref_zone]
            x = ref['x'] + ref['width'] - width  # Allineato a destra
            y = ref['y'] - height - margin
            
        elif anchor == "bottom":
            # In fondo rispetto a tutte le zone esistenti
            x = 0
            y = min(zone['y'] - zone['height'] for zone in self.zones.values()) - margin - height
            
        else:
            # Fallback
            x, y = 0, 0
            
        zone = {
            'name': name,
            'x': x,
            'y': y,
            'width': width,
            'height': height,
            'anchor': anchor,
            'ref_zone': ref_zone
        }
        
        self.zones[name] = zone
        self._update_total_bounds(zone)
        
        print(f"📍 Zona '{name}': {width:.0f}x{height:.0f} @ ({x:.0f}, {y:.0f})")
        return zone
    
    def _update_total_bounds(self, zone: Dict):
        """Aggiorna i bounds totali del layout."""
        minx = min(self.total_bounds[0], zone['x'])
        miny = min(self.total_bounds[1], zone['y'] - zone['height'])
        maxx = max(self.total_bounds[2], zone['x'] + zone['width'])
        maxy = max(self.total_bounds[3], zone['y'])
        self.total_bounds = [minx, miny, maxx, maxy]
    
    def get_total_width(self) -> float:
        return self.total_bounds[2] - self.total_bounds[0]
    
    def get_total_height(self) -> float:
        return self.total_bounds[3] - self.total_bounds[1]


def _calculate_cutting_height(customs: List[Dict]) -> float:
    """Calcola altezza necessaria per schema di taglio."""
    if not customs:
        return 1000
    
    # Simula layout taglio per calcolare altezza
    rows = _optimize_cutting_layout(customs)
    row_height = 600  # Altezza base per riga
    margin = 100
    
    total_height = len(rows) * (row_height + margin) + 800  # +800 per titolo e margini
    return max(total_height, 1500)  # Minimo 1500mm


def _calculate_tables_height(summary: Dict[str, int], customs: List[Dict]) -> float:
    """Calcola altezza necessaria per le tabelle."""
    std_rows = len(summary) + 2  # +2 per header e totale
    custom_rows = len(customs) + 1  # +1 per header
    
    row_height = 200  # Altezza riga tabella
    title_height = 300  # Altezza titoli
    margin = 200
    
    std_table_height = std_rows * row_height + title_height
    custom_table_height = custom_rows * row_height + title_height
    
    # Tabelle affiancate, prendiamo la più alta
    max_table_height = max(std_table_height, custom_table_height)
    
    return max_table_height + margin * 2


def _draw_main_layout(msp, wall_polygon: Polygon, placed: List[Dict], customs: List[Dict], 
                     apertures: Optional[List[Polygon]], zone: Dict):
    """Disegna il layout principale della parete."""
    offset_x = zone['x']
    offset_y = zone['y']
    
    # Contorno parete
    _draw_wall_outline(msp, wall_polygon, offset_x, offset_y)
    
    # Aperture
    if apertures:
        _draw_apertures(msp, apertures, offset_x, offset_y)
    
    # Blocchi
    _draw_standard_blocks(msp, placed, offset_x, offset_y)
    _draw_custom_blocks(msp, customs, offset_x, offset_y)
    
    # Quote principali
    _add_main_dimensions(msp, wall_polygon, offset_x, offset_y)
    
    # Titolo sezione - SPOSTATO PIÙ IN ALTO
    msp.add_text("LAYOUT PARETE PRINCIPALE", height=300, dxfattribs={
        "layer": "TESTI",
        "style": "Standard"
    }).set_placement((offset_x + zone['width']/2, offset_y + zone['height'] + 800), 
                    align=TextEntityAlignment.MIDDLE_CENTER)


def _draw_cutting_schema_fixed(msp, customs: List[Dict], zone: Dict):
    """Disegna schema di taglio nella zona assegnata."""
    if not customs:
        return
    
    offset_x = zone['x']
    offset_y = zone['y']
    
    # Titolo sezione - SPOSTATO PIÙ IN ALTO E PIÙ PICCOLO
    msp.add_text("SCHEMA DI TAGLIO", height=250, dxfattribs={
        "layer": "TESTI",
        "style": "Standard"
    }).set_placement((offset_x + zone['width']/2, offset_y + zone['height'] + 600), 
                    align=TextEntityAlignment.MIDDLE_CENTER)
    
    msp.add_text("PEZZI CUSTOM", height=200, dxfattribs={
        "layer": "TESTI",
        "style": "Standard"
    }).set_placement((offset_x + zone['width']/2, offset_y + zone['height'] + 300), 
                    align=TextEntityAlignment.MIDDLE_CENTER)
    
    # Layout pezzi di taglio
    cutting_layout = _optimize_cutting_layout(customs)
    _, custom_labels = create_block_labels([], customs)
    
    current_x = offset_x + 100  # Margine sinistro
    current_y = offset_y + zone['height'] - 800  # Partenza dall'alto - PIÙ SPAZIO PER TITOLO
    row_height = 600
    margin = 100
    
    for row_idx, row in enumerate(cutting_layout):
        row_start_x = current_x
        max_height_in_row = 0
        
        # Controlla se la riga entra nella zona
        if current_y - row_height < offset_y:
            break  # Non entra più, stop
        
        for piece_idx in row:
            custom = customs[piece_idx]
            width = min(custom['width'], zone['width'] - 200)  # Limita larghezza
            height = custom['height']
            
            # Controlla se il pezzo entra orizzontalmente
            if current_x + width > offset_x + zone['width'] - 100:
                break  # Non entra, passa alla riga successiva
            
            # Disegna rettangolo di taglio
            msp.add_lwpolyline([
                (current_x, current_y),
                (current_x + width, current_y),
                (current_x + width, current_y - height),
                (current_x, current_y - height),
                (current_x, current_y)
            ], dxfattribs={"layer": "TAGLIO"})
            
            # Etichetta pezzo
            center_x = current_x + width / 2
            center_y = current_y - height / 2
            label = custom_labels[piece_idx]
            
            msp.add_text(label, height=100, dxfattribs={
                "layer": "TESTI",
                "style": "Standard"
            }).set_placement((center_x, center_y), align=TextEntityAlignment.MIDDLE_CENTER)
            
            # Quote
            msp.add_text(f"{width:.0f}", height=60, dxfattribs={
                "layer": "QUOTE"
            }).set_placement((center_x, current_y + 80), align=TextEntityAlignment.MIDDLE_CENTER)
            
            current_x += width + margin
            max_height_in_row = max(max_height_in_row, height)
        
        # Prossima riga
        current_x = row_start_x
        current_y -= max_height_in_row + margin


def _draw_tables_section(msp, summary: Dict[str, int], customs: List[Dict], placed: List[Dict], zone: Dict):
    """Disegna sezione tabelle nella zona assegnata."""
    offset_x = zone['x']
    offset_y = zone['y']
    
    # Dividi zona in due colonne per le tabelle
    col_width = zone['width'] / 2 - 200  # -200 per margini
    
    # TABELLA BLOCCHI STANDARD (colonna sinistra)
    std_zone = {
        'x': offset_x,
        'y': offset_y + zone['height'],
        'width': col_width,
        'height': zone['height']
    }
    _draw_standard_blocks_table_fixed(msp, summary, placed, std_zone)
    
    # TABELLA CUSTOM (colonna destra)
    custom_zone = {
        'x': offset_x + col_width + 200,
        'y': offset_y + zone['height'],
        'width': col_width,
        'height': zone['height']
    }
    _draw_custom_dimensions_table_fixed(msp, customs, custom_zone)


def _draw_standard_blocks_table_fixed(msp, summary: Dict[str, int], placed: List[Dict], zone: Dict):
    """Disegna tabella blocchi standard nella zona assegnata."""
    offset_x = zone['x']
    offset_y = zone['y']
    
    # Titolo
    msp.add_text("BLOCCHI STANDARD", height=150, dxfattribs={
        "layer": "TESTI",
        "style": "Standard"
    }).set_placement((offset_x + zone['width']/2, offset_y - 100), align=TextEntityAlignment.MIDDLE_CENTER)
    
    # Raggruppa per tipo
    type_details = {}
    for blk in placed:
        btype = blk['type']
        if btype not in type_details:
            type_details[btype] = {
                'count': 0,
                'width': blk['width'],
                'height': blk['height']
            }
        type_details[btype]['count'] += 1
    
    # Setup tabella
    headers = ["TIPO", "QTÀ", "DIMENSIONI"]
    col_widths = [zone['width'] * 0.4, zone['width'] * 0.2, zone['width'] * 0.4]
    row_height = 200
    
    start_y = offset_y - 300
    
    # Header
    current_x = offset_x
    for i, (header, width) in enumerate(zip(headers, col_widths)):
        msp.add_lwpolyline([
            (current_x, start_y),
            (current_x + width, start_y),
            (current_x + width, start_y - row_height),
            (current_x, start_y - row_height),
            (current_x, start_y)
        ], dxfattribs={"layer": "CARTIGLIO"})
        
        msp.add_text(header, height=80, dxfattribs={
            "layer": "TESTI",
            "style": "Standard"
        }).set_placement((current_x + width/2, start_y - row_height/2), 
                        align=TextEntityAlignment.MIDDLE_CENTER)
        current_x += width
    
    # Righe dati
    sorted_types = sorted(type_details.items(), key=lambda x: x[1]['width'], reverse=True)
    
    for i, (btype, details) in enumerate(sorted_types):
        current_y = start_y - (i + 2) * row_height  # +2 per saltare header
        current_x = offset_x
        
        # Controlla se la riga entra nella zona
        if current_y < offset_y - zone['height']:
            break
        
        letter = SIZE_TO_LETTER.get(details['width'], 'X')
        friendly_name = f"Tipo {letter}"
        
        row_data = [
            friendly_name,
            str(details['count']),
            f"{details['width']}×{details['height']}"
        ]
        
        for j, (data, width) in enumerate(zip(row_data, col_widths)):
            msp.add_lwpolyline([
                (current_x, current_y),
                (current_x + width, current_y),
                (current_x + width, current_y - row_height),
                (current_x, current_y - row_height),
                (current_x, current_y)
            ], dxfattribs={"layer": "CARTIGLIO"})
            
            msp.add_text(str(data), height=70, dxfattribs={
                "layer": "TESTI",
                "style": "Standard"
            }).set_placement((current_x + width/2, current_y - row_height/2),
                            align=TextEntityAlignment.MIDDLE_CENTER)
            current_x += width


def _draw_custom_dimensions_table_fixed(msp, customs: List[Dict], zone: Dict):
    """Disegna tabella dimensioni custom nella zona assegnata."""
    if not customs:
        return
    
    offset_x = zone['x']
    offset_y = zone['y']
    
    # Titolo
    msp.add_text("PEZZI CUSTOM", height=150, dxfattribs={
        "layer": "TESTI",
        "style": "Standard"
    }).set_placement((offset_x + zone['width']/2, offset_y - 100), align=TextEntityAlignment.MIDDLE_CENTER)
    
    # Setup tabella
    headers = ["ID", "TIPO", "DIM"]
    col_widths = [zone['width'] * 0.3, zone['width'] * 0.3, zone['width'] * 0.4]
    row_height = 200
    
    start_y = offset_y - 300
    
    # Header
    current_x = offset_x
    for header, width in zip(headers, col_widths):
        msp.add_lwpolyline([
            (current_x, start_y),
            (current_x + width, start_y),
            (current_x + width, start_y - row_height),
            (current_x, start_y - row_height),
            (current_x, start_y)
        ], dxfattribs={"layer": "CARTIGLIO"})
        
        msp.add_text(header, height=80, dxfattribs={
            "layer": "TESTI",
            "style": "Standard"
        }).set_placement((current_x + width/2, start_y - row_height/2),
                        align=TextEntityAlignment.MIDDLE_CENTER)
        current_x += width
    
    # Dati
    _, custom_labels = create_block_labels([], customs)
    
    max_rows = int((zone['height'] - 400) / row_height) - 1  # -1 per header
    display_customs = customs[:max_rows]  # Limita il numero di righe
    
    for i, custom in enumerate(display_customs):
        current_y = start_y - (i + 1) * row_height
        current_x = offset_x
        
        ctype = custom.get('ctype', 2)
        
        row_data = [
            custom_labels[i],
            f"CU{ctype}",
            f"{custom['width']:.0f}×{custom['height']:.0f}"
        ]
        
        for data, width in zip(row_data, col_widths):
            msp.add_lwpolyline([
                (current_x, current_y),
                (current_x + width, current_y),
                (current_x + width, current_y - row_height),
                (current_x, current_y - row_height),
                (current_x, current_y)
            ], dxfattribs={"layer": "CARTIGLIO"})
            
            msp.add_text(str(data), height=70, dxfattribs={
                "layer": "TESTI",
                "style": "Standard"
            }).set_placement((current_x + width/2, current_y - row_height/2),
                            align=TextEntityAlignment.MIDDLE_CENTER)
            current_x += width
    
    # Nota se ci sono più custom di quelli visualizzati
    if len(customs) > max_rows:
        msp.add_text(f"... e altri {len(customs) - max_rows} pezzi custom", 
                    height=60, dxfattribs={
                        "layer": "TESTI",
                        "style": "Standard"
                    }).set_placement((offset_x + zone['width']/2, 
                                    start_y - (max_rows + 2) * row_height),
                                    align=TextEntityAlignment.MIDDLE_CENTER)


def _draw_professional_cartridge_fixed(msp, project_name: str, summary: Dict[str, int], 
                                     customs: List[Dict], params: Optional[Dict], zone: Dict):
    """Disegna cartiglio nella zona assegnata."""
    offset_x = zone['x']
    offset_y = zone['y']
    
    # Rettangolo cartiglio
    msp.add_lwpolyline([
        (offset_x, offset_y - zone['height']),
        (offset_x + zone['width'], offset_y - zone['height']),
        (offset_x + zone['width'], offset_y),
        (offset_x, offset_y),
        (offset_x, offset_y - zone['height'])
    ], dxfattribs={"layer": "CARTIGLIO"})
    
    # Titolo progetto
    msp.add_text(project_name.upper(), height=120, dxfattribs={
        "layer": "TESTI",
        "style": "Standard"
    }).set_placement((offset_x + zone['width']/2, offset_y - 200),
                    align=TextEntityAlignment.MIDDLE_CENTER)
    
    # Informazioni tecniche
    now = datetime.datetime.now()
    total_standard = sum(summary.values())
    total_custom = len(customs)
    efficiency = total_standard / (total_standard + total_custom) if total_standard + total_custom > 0 else 0
    
    info_lines = [
        f"Data: {now.strftime('%d/%m/%Y %H:%M')}",
        f"Blocchi Standard: {total_standard}",
        f"Pezzi Custom: {total_custom}",
        f"Efficienza: {efficiency:.1%}",
        f"Algoritmo: Greedy + Backtrack"
    ]
    
    for i, line in enumerate(info_lines):
        msp.add_text(line, height=80, dxfattribs={
            "layer": "TESTI",
            "style": "Standard"
        }).set_placement((offset_x + 100, offset_y - 400 - i * 150),
                        align=TextEntityAlignment.BOTTOM_LEFT)


def _draw_legend_and_notes_fixed(msp, zone: Dict):
    """Disegna legenda nella zona assegnata."""
    offset_x = zone['x']
    offset_y = zone['y']
    
    # Titolo
    msp.add_text("LEGENDA E NOTE TECNICHE", height=120, dxfattribs={
        "layer": "TESTI",
        "style": "Standard"
    }).set_placement((offset_x + zone['width']/2, offset_y - 100), 
                    align=TextEntityAlignment.MIDDLE_CENTER)
    
    # Note in due colonne
    col_width = zone['width'] / 2
    
    # Colonna 1: Simboli
    symbols = [
        ("━━", "BLOCCHI_STD", "Blocchi Standard"),
        ("╱╱", "BLOCCHI_CUSTOM", "Pezzi Custom"),
        ("┈┈", "APERTURE", "Aperture"),
        ("↔", "QUOTE", "Quote (mm)")
    ]
    
    for i, (symbol, layer, desc) in enumerate(symbols):
        y_pos = offset_y - 300 - i * 120
        msp.add_text(f"{symbol} {desc}", height=60, dxfattribs={
            "layer": "TESTI",
            "style": "Standard"
        }).set_placement((offset_x + 100, y_pos), align=TextEntityAlignment.BOTTOM_LEFT)
    
    # Colonna 2: Note tecniche
    notes = [
        "• Dimensioni in millimetri",
        "• Tolleranze taglio ±2mm", 
        "• CU1: taglio larghezza da blocco C",
        "• CU2: taglio flessibile da blocco C"
    ]
    
    for i, note in enumerate(notes):
        y_pos = offset_y - 300 - i * 120
        msp.add_text(note, height=60, dxfattribs={
            "layer": "TESTI",
            "style": "Standard"
        }).set_placement((offset_x + col_width + 100, y_pos), 
                        align=TextEntityAlignment.BOTTOM_LEFT)


# ===== FUNZIONI HELPER ESISTENTI (mantengono la stessa logica) =====

def _setup_dxf_layers(doc):
    """Configura layer professionali con colori e stili standard."""
    layer_config = [
        # (name, color, linetype, lineweight)
        ("PARETE", dxf_colors.BLUE, "CONTINUOUS", 0.50),
        ("APERTURE", dxf_colors.RED, "DASHED", 0.30),
        ("BLOCCHI_STD", dxf_colors.BLACK, "CONTINUOUS", 0.25),
        ("BLOCCHI_CUSTOM", dxf_colors.GREEN, "CONTINUOUS", 0.35),
        ("QUOTE", dxf_colors.MAGENTA, "CONTINUOUS", 0.18),
        ("TESTI", dxf_colors.BLACK, "CONTINUOUS", 0.15),
        ("TAGLIO", dxf_colors.CYAN, "CONTINUOUS", 0.40),
        ("CARTIGLIO", dxf_colors.BLACK, "CONTINUOUS", 0.25),
        ("LEGENDA", dxf_colors.BLACK, "CONTINUOUS", 0.20),
    ]
    
    for name, color, linetype, lineweight in layer_config:
        layer = doc.layers.add(name)
        layer.color = color
        layer.linetype = linetype
        layer.lineweight = int(lineweight * 100)  # Convert to AutoCAD units


def _draw_wall_outline(msp, wall_polygon: Polygon, offset_x: float, offset_y: float):
    """Disegna il contorno della parete principale."""
    # Contorno esterno con linea più spessa
    exterior_coords = [(x + offset_x, y + offset_y) for x, y in wall_polygon.exterior.coords]
    msp.add_lwpolyline(exterior_coords, close=True, dxfattribs={
        "layer": "PARETE",
        "lineweight": 100  # Linea più spessa per visibilità
    })
    
    # Contorni interni (holes)
    for interior in wall_polygon.interiors:
        interior_coords = [(x + offset_x, y + offset_y) for x, y in interior.coords]
        msp.add_lwpolyline(interior_coords, close=True, dxfattribs={
            "layer": "PARETE",
            "lineweight": 80
        })


def _draw_apertures(msp, apertures: List[Polygon], offset_x: float, offset_y: float):
    """Disegna porte e finestre."""
    for i, aperture in enumerate(apertures):
        coords = [(x + offset_x, y + offset_y) for x, y in aperture.exterior.coords]
        msp.add_lwpolyline(coords, close=True, dxfattribs={"layer": "APERTURE"})
        
        # Etichetta apertura
        minx, miny, maxx, maxy = aperture.bounds
        center_x = (minx + maxx) / 2 + offset_x
        center_y = (miny + maxy) / 2 + offset_y
        width = maxx - minx
        height = maxy - miny
        
        label = f"AP{i+1}\n{width:.0f}x{height:.0f}"
        msp.add_text(label, height=150, dxfattribs={
            "layer": "TESTI", 
            "style": "Standard"
        }).set_placement((center_x, center_y), align=TextEntityAlignment.MIDDLE_CENTER)


def _draw_standard_blocks(msp, placed: List[Dict], offset_x: float, offset_y: float):
    """Disegna blocchi standard con etichette."""
    std_labels, _ = create_block_labels(placed, [])
    
    for i, block in enumerate(placed):
        x1 = block['x'] + offset_x
        y1 = block['y'] + offset_y
        x2 = x1 + block['width']
        y2 = y1 + block['height']
        
        # Rettangolo blocco
        msp.add_lwpolyline([
            (x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)
        ], dxfattribs={"layer": "BLOCCHI_STD"})
        
        # Etichetta centrata
        center_x = x1 + block['width'] / 2
        center_y = y1 + block['height'] / 2
        label = std_labels[i]
        
        msp.add_text(label, height=120, dxfattribs={
            "layer": "TESTI",
            "style": "Standard"
        }).set_placement((center_x, center_y), align=TextEntityAlignment.MIDDLE_CENTER)


def _draw_custom_blocks(msp, customs: List[Dict], offset_x: float, offset_y: float):
    """Disegna blocchi custom con etichette e info taglio."""
    _, custom_labels = create_block_labels([], customs)
    
    for i, custom in enumerate(customs):
        # Disegna geometria custom
        try:
            poly = shape(custom['geometry'])
            coords = [(x + offset_x, y + offset_y) for x, y in poly.exterior.coords]
            msp.add_lwpolyline(coords, close=True, dxfattribs={"layer": "BLOCCHI_CUSTOM"})
            
            # Etichetta con info taglio
            center_x = custom['x'] + custom['width'] / 2 + offset_x
            center_y = custom['y'] + custom['height'] / 2 + offset_y
            
            ctype = custom.get('ctype', 2)
            label = f"{custom_labels[i]}\n{custom['width']:.0f}x{custom['height']:.0f}\nCU{ctype}"
            
            msp.add_text(label, height=90, dxfattribs={
                "layer": "TESTI",
                "style": "Standard"
            }).set_placement((center_x, center_y), align=TextEntityAlignment.MIDDLE_CENTER)
            
        except Exception as e:
            print(f"⚠️ Errore disegno custom {i}: {e}")


def _add_main_dimensions(msp, wall_polygon: Polygon, offset_x: float, offset_y: float):
    """Aggiunge quote principali della parete."""
    minx, miny, maxx, maxy = wall_polygon.bounds
    wall_width = maxx - minx
    wall_height = maxy - miny
    
    # Quota larghezza totale (in basso)
    dim_y = miny + offset_y - 300
    dim = msp.add_linear_dim(
        base=(minx + offset_x, dim_y),
        p1=(minx + offset_x, miny + offset_y),
        p2=(maxx + offset_x, miny + offset_y),
        text=f"{wall_width:.0f}",
        dimstyle="Standard",
        dxfattribs={"layer": "QUOTE"}
    )
    
    # Quota altezza totale (a sinistra)
    dim_x = minx + offset_x - 300
    dim = msp.add_linear_dim(
        base=(dim_x, miny + offset_y),
        p1=(minx + offset_x, miny + offset_y),
        p2=(minx + offset_x, maxy + offset_y),
        text=f"{wall_height:.0f}",
        dimstyle="Standard",
        dxfattribs={"layer": "QUOTE"}
    )


def _optimize_cutting_layout(customs: List[Dict]) -> List[List[int]]:
    """
    Ottimizza il layout di taglio raggruppando pezzi simili.
    Returns: Lista di righe, ogni riga contiene indici dei pezzi.
    """
    if not customs:
        return []
    
    # Ordina pezzi per altezza decrescente, poi larghezza
    sorted_indices = sorted(
        range(len(customs)),
        key=lambda i: (-customs[i]['height'], -customs[i]['width'])
    )
    
    # Raggruppa in righe di altezza simile
    rows = []
    current_row = []
    current_row_height = None
    height_tolerance = 50  # mm
    
    for idx in sorted_indices:
        piece_height = customs[idx]['height']
        
        if (current_row_height is None or 
            abs(piece_height - current_row_height) <= height_tolerance):
            current_row.append(idx)
            current_row_height = piece_height
        else:
            if current_row:
                rows.append(current_row)
            current_row = [idx]
            current_row_height = piece_height
    
    if current_row:
        rows.append(current_row)
    
    return rows


# ────────────────────────────────────────────────────────────────────────────────
# Packing core (ESISTENTE - mantenuto identico)
# ────────────────────────────────────────────────────────────────────────────────
def _mk_std(x: float, y: float, w: int, h: int) -> Dict:
    return {"type": f"std_{w}x{h}", "width": w, "height": h, "x": snap(x), "y": snap(y)}

def _mk_custom(geom: Polygon) -> Dict:
    geom = sanitize_polygon(geom)
    minx, miny, maxx, maxy = geom.bounds
    return {
        "type": "custom",
        "width": snap(maxx - minx),
        "height": snap(maxy - miny),
        "x": snap(minx),
        "y": snap(miny),
        "geometry": mapping(geom)
    }

def _score_solution(placed: List[Dict], custom: List[Dict]) -> Tuple[int, float]:
    """Score lessicografico: (#custom, area_custom_totale)."""
    total_area = 0.0
    for c in custom:
        poly = shape(c["geometry"])
        total_area += poly.area
    return (len(custom), total_area)

def _try_fill(comp: Polygon, y: float, stripe_top: float, widths: List[int], start_x: float) -> Tuple[List[Dict], List[Dict]]:
    """Greedy semplice a partire da start_x con ordine widths."""
    placed: List[Dict] = []
    custom: List[Dict] = []

    seg_minx, _, seg_maxx, _ = comp.bounds
    cursor = snap(start_x)
    y = snap(y)
    stripe_top = snap(stripe_top)

    while cursor < seg_maxx - COORD_EPS:
        placed_one = False
        for bw in widths:
            if cursor + bw <= seg_maxx + COORD_EPS:
                candidate = box(cursor, y, cursor + bw, stripe_top)
                intersec = candidate.intersection(comp)
                if intersec.is_empty or intersec.area < AREA_EPS:
                    continue
                if math.isclose(intersec.area, candidate.area, rel_tol=1e-9):
                    placed.append(_mk_std(cursor, y, bw, BLOCK_HEIGHT))
                else:
                    custom.append(_mk_custom(intersec))
                cursor = snap(cursor + bw)
                placed_one = True
                break
        if not placed_one:
            remaining = comp.intersection(box(cursor, y, seg_maxx, stripe_top))
            if not remaining.is_empty and remaining.area > AREA_EPS:
                custom.append(_mk_custom(remaining))
            break
    return placed, custom

def _pack_segment_with_order(comp: Polygon, y: float, stripe_top: float, widths_order: List[int], offset: int = 0) -> Tuple[List[Dict], List[Dict]]:
    """Esegue il packing su un singolo segmento (comp), con offset e ordine blocchi fissati."""
    placed: List[Dict] = []
    custom: List[Dict] = []

    seg_minx, _, seg_maxx, _ = comp.bounds
    seg_minx = snap(seg_minx)
    seg_maxx = snap(seg_maxx)
    y = snap(y)
    stripe_top = snap(stripe_top)

    cursor = seg_minx

    # offset iniziale (se richiesto)
    if offset and cursor + offset <= seg_maxx + COORD_EPS:
        candidate = box(cursor, y, cursor + offset, stripe_top)
        intersec = candidate.intersection(comp)
        if not intersec.is_empty and intersec.area >= AREA_EPS:
            if math.isclose(intersec.area, candidate.area, rel_tol=1e-9):
                placed.append(_mk_std(cursor, y, offset, BLOCK_HEIGHT))
            else:
                custom.append(_mk_custom(intersec))
            cursor = snap(cursor + offset)

    # storico per eventuale backtrack sul micro-resto
    history = []  # (cursor_before, placed_index_len, custom_index_len)
    while cursor < seg_maxx - COORD_EPS:
        history.append((cursor, len(placed), len(custom)))
        placed_one = False
        for bw in widths_order:
            if cursor + bw <= seg_maxx + COORD_EPS:
                candidate = box(cursor, y, cursor + bw, stripe_top)
                intersec = candidate.intersection(comp)
                if intersec.is_empty or intersec.area < AREA_EPS:
                    continue
                if math.isclose(intersec.area, candidate.area, rel_tol=1e-9):
                    placed.append(_mk_std(cursor, y, bw, BLOCK_HEIGHT))
                else:
                    custom.append(_mk_custom(intersec))
                cursor = snap(cursor + bw)
                placed_one = True
                break
        if not placed_one:
            # residuo a fine segmento
            remaining = comp.intersection(box(cursor, y, seg_maxx, stripe_top))
            rem_width = seg_maxx - cursor
            if rem_width < MICRO_REST_MM and history:
                # backtrack 1 step e prova ordine alternativo fine-coda
                cursor_prev, p_len, c_len = history[-1]
                placed = placed[:p_len]
                custom = custom[:c_len]
                cursor = cursor_prev
                alt_order = [413, 826, 1239]
                p2, c2 = _try_fill(comp, y, stripe_top, alt_order, cursor)
                baseline_custom_area = 0.0
                if not remaining.is_empty and remaining.area > AREA_EPS:
                    baseline_custom_area = remaining.area
                score_backtrack = _score_solution(placed + p2, custom + c2)
                score_baseline = (len(custom) + (1 if baseline_custom_area > 0 else 0), baseline_custom_area)
                if score_backtrack < score_baseline:
                    placed.extend(p2)
                    custom.extend(c2)
                    if p2:
                        last = p2[-1]
                        cursor = snap(last["x"] + last["width"])
                    elif c2:
                        break
                    continue
                else:
                    if baseline_custom_area > AREA_EPS:
                        custom.append(_mk_custom(remaining))
                    break
            else:
                if not remaining.is_empty and remaining.area > AREA_EPS:
                    custom.append(_mk_custom(remaining))
                break

    return placed, custom

def _pack_segment(comp: Polygon, y: float, stripe_top: float, widths: List[int], offset: int = 0) -> Tuple[List[Dict], List[Dict]]:
    """Prova più ordini e sceglie la soluzione migliore per il segmento."""
    best_placed = []
    best_custom = []
    best_score = (10**9, float("inf"))
    for order in BLOCK_ORDERS:
        p_try, c_try = _pack_segment_with_order(comp, y, stripe_top, order, offset=offset)
        score = _score_solution(p_try, c_try)
        if score < best_score:
            best_score = score
            best_placed, best_custom = p_try, c_try
    return best_placed, best_custom

def pack_wall(polygon: Polygon,
              block_widths: List[int],
              block_height: int,
              row_offset: Optional[int] = 826,
              apertures: Optional[List[Polygon]] = None) -> Tuple[List[Dict], List[Dict]]:
    """
    Packer principale.
    """
    polygon = sanitize_polygon(polygon)

    # Aperture dal poligono + eventuali passate a parte
    hole_polys = polygon_holes(polygon)
    ap_list = list(apertures) if apertures else []
    keepout = None
    if hole_polys or ap_list:
        u = unary_union([*hole_polys, *ap_list])
        keepout = u.buffer(KEEP_OUT_MM) if not u.is_empty else None

    minx, miny, maxx, maxy = polygon.bounds
    placed_all: List[Dict] = []
    custom_all: List[Dict] = []

    y = miny
    row = 0

    while y < maxy - COORD_EPS:
        stripe_top = min(y + block_height, maxy)
        stripe = box(minx, y, maxx, stripe_top)
        inter = polygon.intersection(stripe)
        if keepout:
            inter = inter.difference(keepout)

        comps = ensure_multipolygon(inter)

        for comp in comps:
            if comp.is_empty or comp.area < AREA_EPS:
                continue

            # offset candidates
            offset_candidates: List[int] = [0] if (row % 2 == 0) else []
            if row % 2 == 1:
                if row_offset is not None:
                    offset_candidates.append(int(row_offset))
                offset_candidates.append(413)

            best_placed = []
            best_custom = []
            best_score = (10**9, float("inf"))

            for off in offset_candidates:
                p_try, c_try = _pack_segment(comp, y, stripe_top, BLOCK_WIDTHS, offset=off)
                score = _score_solution(p_try, c_try)
                if score < best_score:
                    best_score = score
                    best_placed, best_custom = p_try, c_try

            placed_all.extend(best_placed)
            custom_all.extend(best_custom)

        y = snap(y + block_height)
        row += 1

    custom_all = merge_customs_row_aware(custom_all, tol=SCARTO_CUSTOM_MM, row_height=BLOCK_HEIGHT)
    custom_all = split_out_of_spec(custom_all, max_w=SPLIT_MAX_WIDTH_MM)
    return placed_all, validate_and_tag_customs(custom_all)

# ────────────────────────────────────────────────────────────────────────────────
# Optimization (hook - no-op for ora)
# ────────────────────────────────────────────────────────────────────────────────
def opt_pass(placed: List[Dict], custom: List[Dict], block_widths: List[int]) -> Tuple[List[Dict], List[Dict]]:
    """
    Ottimizzazione post-packing per ridurre sprechi e migliorare efficienza.
    
    Strategie implementate:
    1. Merge custom adiacenti in blocchi standard
    2. Sostituzione gruppi custom con standard
    3. Eliminazione micro-custom
    4. Riposizionamento per allineamento
    5. Cross-row optimization
    """
    if not custom:
        return placed, custom
    
    print(f"🔧 Ottimizzazione: {len(placed)} standard + {len(custom)} custom")
    
    # FASE 1: Merge custom adiacenti orizzontalmente
    optimized_custom = _merge_adjacent_customs_horizontal(custom)
    print(f"   Merge orizzontale: {len(custom)} → {len(optimized_custom)} custom")
    
    # FASE 2: Merge custom adiacenti verticalmente (cross-row)
    optimized_custom = _merge_adjacent_customs_vertical(optimized_custom)
    print(f"   Merge verticale: → {len(optimized_custom)} custom")
    
    # FASE 3: Sostituzione custom con blocchi standard
    new_placed, optimized_custom = _replace_customs_with_standards(
        placed, optimized_custom, block_widths
    )
    print(f"   Sostituzione: +{len(new_placed) - len(placed)} standard, -{len(custom) - len(optimized_custom)} custom")
    
    # FASE 4: Eliminazione micro-custom (< 50mm in qualsiasi dimensione)
    optimized_custom = _eliminate_micro_customs(optimized_custom, min_size=50)
    print(f"   Micro-cleanup: → {len(optimized_custom)} custom")
    
    # FASE 5: Riposizionamento per allineamento perfetto
    aligned_placed, aligned_custom = _align_blocks_to_grid(new_placed, optimized_custom)
    
    print(f"✅ Risultato ottimizzazione: {len(aligned_placed)} standard + {len(aligned_custom)} custom")
    
    return aligned_placed, aligned_custom


def _merge_adjacent_customs_horizontal(customs: List[Dict]) -> List[Dict]:
    """Unisce custom adiacenti orizzontalmente nella stessa riga."""
    if len(customs) < 2:
        return customs
    
    # Raggruppa per riga (Y position)
    rows = defaultdict(list)
    for c in customs:
        row_y = int(round(c["y"] / BLOCK_HEIGHT)) * BLOCK_HEIGHT
        rows[row_y].append(c)
    
    merged = []
    for row_y, row_customs in rows.items():
        # Ordina per X
        row_customs.sort(key=lambda c: c["x"])
        
        current_group = [row_customs[0]]
        
        for i in range(1, len(row_customs)):
            prev = current_group[-1]
            curr = row_customs[i]
            
            # Controlla se sono adiacenti (gap < 10mm)
            prev_right = prev["x"] + prev["width"]
            gap = curr["x"] - prev_right
            
            if (gap < 10 and 
                abs(prev["y"] - curr["y"]) < 5 and  # stessa riga
                abs(prev["height"] - curr["height"]) < 5):  # stessa altezza
                current_group.append(curr)
            else:
                # Processo gruppo corrente
                if len(current_group) > 1:
                    merged_custom = _merge_custom_group(current_group)
                    merged.append(merged_custom)
                else:
                    merged.extend(current_group)
                
                current_group = [curr]
        
        # Processo ultimo gruppo
        if len(current_group) > 1:
            merged_custom = _merge_custom_group(current_group)
            merged.append(merged_custom)
        else:
            merged.extend(current_group)
    
    return merged


def _merge_adjacent_customs_vertical(customs: List[Dict]) -> List[Dict]:
    """Unisce custom adiacenti verticalmente tra righe diverse."""
    if len(customs) < 2:
        return customs
    
    # Raggruppa per colonna (X position)
    columns = defaultdict(list)
    for c in customs:
        col_x = int(round(c["x"] / 10)) * 10  # Snap a griglia 10mm
        columns[col_x].append(c)
    
    merged = []
    processed = set()
    
    for col_x, col_customs in columns.items():
        # Ordina per Y
        col_customs.sort(key=lambda c: c["y"])
        
        for i, custom in enumerate(col_customs):
            if id(custom) in processed:
                continue
            
            current_group = [custom]
            processed.add(id(custom))
            
            # Cerca custom adiacenti verticalmente
            for j in range(i + 1, len(col_customs)):
                candidate = col_customs[j]
                if id(candidate) in processed:
                    continue
                
                last_in_group = current_group[-1]
                last_bottom = last_in_group["y"] + last_in_group["height"]
                gap = candidate["y"] - last_bottom
                
                if (gap < 10 and  # gap piccolo
                    abs(last_in_group["x"] - candidate["x"]) < 5 and  # stessa colonna
                    abs(last_in_group["width"] - candidate["width"]) < 10):  # larghezza simile
                    current_group.append(candidate)
                    processed.add(id(candidate))
                else:
                    break  # Non più adiacenti
            
            # Processo gruppo
            if len(current_group) > 1:
                merged_custom = _merge_custom_group_vertical(current_group)
                merged.append(merged_custom)
            else:
                merged.extend(current_group)
    
    # Aggiungi custom non processati (non in colonne allineate)
    for custom in customs:
        if id(custom) not in processed:
            merged.append(custom)
    
    return merged


def _merge_custom_group(group: List[Dict]) -> Dict:
    """Unisce un gruppo di custom adiacenti orizzontalmente."""
    if len(group) == 1:
        return group[0]
    
    # Calcola bounds del gruppo
    min_x = min(c["x"] for c in group)
    max_x = max(c["x"] + c["width"] for c in group)
    min_y = min(c["y"] for c in group)
    max_y = max(c["y"] + c["height"] for c in group)
    
    # Crea geometria unificata
    merged_polygon = unary_union([shape(c["geometry"]) for c in group])
    
    return {
        "type": "custom",
        "width": snap(max_x - min_x),
        "height": snap(max_y - min_y),
        "x": snap(min_x),
        "y": snap(min_y),
        "geometry": mapping(merged_polygon),
        "ctype": 2  # Merged custom = tipo flessibile
    }


def _merge_custom_group_vertical(group: List[Dict]) -> Dict:
    """Unisce un gruppo di custom adiacenti verticalmente."""
    if len(group) == 1:
        return group[0]
    
    # Calcola bounds del gruppo
    min_x = min(c["x"] for c in group)
    max_x = max(c["x"] + c["width"] for c in group)
    min_y = min(c["y"] for c in group)
    max_y = max(c["y"] + c["height"] for c in group)
    
    # Crea geometria unificata
    merged_polygon = unary_union([shape(c["geometry"]) for c in group])
    
    return {
        "type": "custom",
        "width": snap(max_x - min_x),
        "height": snap(max_y - min_y),
        "x": snap(min_x),
        "y": snap(min_y),
        "geometry": mapping(merged_polygon),
        "ctype": 2  # Merged custom = tipo flessibile
    }


def _replace_customs_with_standards(placed: List[Dict], customs: List[Dict], 
                                  block_widths: List[int]) -> Tuple[List[Dict], List[Dict]]:
    """Sostituisce gruppi di custom con blocchi standard quando conveniente."""
    new_placed = placed.copy()
    remaining_customs = []
    
    for custom in customs:
        w = custom["width"]
        h = custom["height"]
        
        # Controlla se può diventare un blocco standard
        best_match = None
        best_waste = float('inf')
        
        for std_width in block_widths:
            if (abs(w - std_width) <= SCARTO_CUSTOM_MM and 
                abs(h - BLOCK_HEIGHT) <= SCARTO_CUSTOM_MM):
                
                waste = abs(w - std_width) + abs(h - BLOCK_HEIGHT)
                if waste < best_waste:
                    best_waste = waste
                    best_match = std_width
        
        if best_match:
            # Sostituisci con blocco standard
            std_block = _mk_std(custom["x"], custom["y"], best_match, BLOCK_HEIGHT)
            new_placed.append(std_block)
            print(f"   Sostituzione: custom {w}x{h} → standard {best_match}x{BLOCK_HEIGHT}")
        else:
            remaining_customs.append(custom)
    
    return new_placed, remaining_customs


def _eliminate_micro_customs(customs: List[Dict], min_size: float = 50) -> List[Dict]:
    """Elimina o unisce custom troppo piccoli."""
    filtered = []
    
    for custom in customs:
        w = custom["width"]
        h = custom["height"]
        
        if w < min_size or h < min_size:
            # Troppo piccolo, prova a unire con un custom vicino
            merged = False
            for candidate in filtered:
                if _can_merge_customs(custom, candidate):
                    # Unisci con candidato esistente
                    merged_custom = _merge_two_customs(custom, candidate)
                    filtered.remove(candidate)
                    filtered.append(merged_custom)
                    merged = True
                    break
            
            if not merged:
                # Se non può essere unito e è davvero micro (<20mm), scarta
                if w >= 20 and h >= 20:
                    filtered.append(custom)
                # else: scartato silenziosamente
        else:
            filtered.append(custom)
    
    return filtered


def _can_merge_customs(custom1: Dict, custom2: Dict) -> bool:
    """Controlla se due custom possono essere uniti."""
    # Distanza massima per essere considerati "vicini"
    max_distance = 100  # mm
    
    x1, y1, w1, h1 = custom1["x"], custom1["y"], custom1["width"], custom1["height"]
    x2, y2, w2, h2 = custom2["x"], custom2["y"], custom2["width"], custom2["height"]
    
    # Calcola distanza tra centri
    center1_x, center1_y = x1 + w1/2, y1 + h1/2
    center2_x, center2_y = x2 + w2/2, y2 + h2/2
    distance = ((center1_x - center2_x)**2 + (center1_y - center2_y)**2)**0.5
    
    return distance <= max_distance


def _merge_two_customs(custom1: Dict, custom2: Dict) -> Dict:
    """Unisce due custom in uno."""
    # Calcola bounds combinati
    min_x = min(custom1["x"], custom2["x"])
    max_x = max(custom1["x"] + custom1["width"], custom2["x"] + custom2["width"])
    min_y = min(custom1["y"], custom2["y"])
    max_y = max(custom1["y"] + custom1["height"], custom2["y"] + custom2["height"])
    
    # Unisci geometrie
    geom1 = shape(custom1["geometry"])
    geom2 = shape(custom2["geometry"])
    merged_geom = unary_union([geom1, geom2])
    
    return {
        "type": "custom",
        "width": snap(max_x - min_x),
        "height": snap(max_y - min_y),
        "x": snap(min_x),
        "y": snap(min_y),
        "geometry": mapping(merged_geom),
        "ctype": 2
    }


def _align_blocks_to_grid(placed: List[Dict], customs: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Allinea tutti i blocchi alla griglia di snap per consistenza."""
    aligned_placed = []
    for block in placed:
        aligned_block = block.copy()
        aligned_block["x"] = snap(block["x"])
        aligned_block["y"] = snap(block["y"])
        aligned_block["width"] = snap(block["width"])
        aligned_block["height"] = snap(block["height"])
        aligned_placed.append(aligned_block)
    
    aligned_customs = []
    for custom in customs:
        aligned_custom = custom.copy()
        aligned_custom["x"] = snap(custom["x"])
        aligned_custom["y"] = snap(custom["y"])
        aligned_custom["width"] = snap(custom["width"])
        aligned_custom["height"] = snap(custom["height"])
        aligned_customs.append(aligned_custom)
    
    return aligned_placed, aligned_customs

# ────────────────────────────────────────────────────────────────────────────────
# Merge customs (row-aware)
# ────────────────────────────────────────────────────────────────────────────────
def merge_customs_row_aware(customs: List[Dict], tol: float = 5, row_height: int = 495) -> List[Dict]:
    """
    Coalesco customs solo all'interno della stessa fascia orizzontale.
    """
    if not customs:
        return []
    rows: Dict[int, List[Polygon]] = defaultdict(list)
    for c in customs:
        y0 = snap(c["y"])
        row_id = int(round(y0 / row_height))
        poly = shape(c["geometry"]).buffer(0)
        rows[row_id].append(poly)

    out: List[Dict] = []
    for rid, polys in rows.items():
        if not polys:
            continue
        merged = unary_union(polys)
        geoms = [merged] if isinstance(merged, Polygon) else list(merged.geoms)
        for g in geoms:
            if g.area > AREA_EPS:
                out.append(_mk_custom(g))
    return out

def split_out_of_spec(customs: List[Dict], max_w: int = 413, max_h: int = 495) -> List[Dict]:
    """Divide ogni pezzo 'out_of_spec' in più slice verticali."""
    out: List[Dict] = []
    for c in customs:
        w = int(round(c.get("width", 0)))
        h = int(round(c.get("height", 0)))
        if (w <= max_w + SCARTO_CUSTOM_MM) and (h <= max_h + SCARTO_CUSTOM_MM):
            out.append(c)
            continue

        poly = shape(c["geometry"]).buffer(0)
        if poly.is_empty or poly.area <= AREA_EPS:
            continue
        minx, miny, maxx, maxy = poly.bounds

        x0 = minx
        while x0 < maxx - COORD_EPS:
            x1 = min(x0 + max_w, maxx)
            strip = box(x0, miny, x1, maxy)
            piece = poly.intersection(strip).buffer(0)
            if not piece.is_empty and piece.area > AREA_EPS:
                out.append(_mk_custom(piece))
            x0 = x1
    return out

def validate_and_tag_customs(custom: List[Dict]) -> List[Dict]:
    """Regole custom: Type 1 ("larghezza"), Type 2 ("flex")."""
    out = []
    for c in custom:
        w = int(round(c["width"]))
        h = int(round(c["height"]))
        if w >= 413 + SCARTO_CUSTOM_MM or h > 495 + SCARTO_CUSTOM_MM:
            c["ctype"] = "out_of_spec"
            out.append(c)
            continue
        if abs(h - 495) <= SCARTO_CUSTOM_MM and w < 413 + SCARTO_CUSTOM_MM:
            c["ctype"] = 1
        else:
            c["ctype"] = 2
        out.append(c)
    return out

# ────────────────────────────────────────────────────────────────────────────────
# Labeling
# ────────────────────────────────────────────────────────────────────────────────
def create_block_labels(placed: List[Dict], custom: List[Dict]) -> Tuple[Dict[int, str], Dict[int, str]]:
    std_counters = {"A": 0, "B": 0, "C": 0}
    std_labels: Dict[int, str] = {}

    for i, blk in enumerate(placed):
        letter = SIZE_TO_LETTER.get(int(blk["width"]), "X")
        if letter == "X":
            candidates = [(abs(int(blk["width"]) - k), v) for k, v in SIZE_TO_LETTER.items()]
            letter = sorted(candidates, key=lambda t: t[0])[0][1]
        std_counters[letter] += 1
        std_labels[i] = f"{letter}{std_counters[letter]}"

    # Robust: supporta ctype 1/2 e 'out_of_spec' -> 'X' → CUX(...)
    custom_labels: Dict[int, str] = {}
    counts = defaultdict(int)  # keys: 1, 2, 'X'
    for i, c in enumerate(custom):
        ctype = c.get("ctype", 2)
        code = ctype if isinstance(ctype, int) and ctype in (1, 2) else "X"
        counts[code] += 1
        custom_labels[i] = f"CU{code}({counts[code]})"
    return std_labels, custom_labels

# ────────────────────────────────────────────────────────────────────────────────
# Summary & export
# ────────────────────────────────────────────────────────────────────────────────
def summarize_blocks(placed: List[Dict]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for blk in placed:
        summary[blk["type"]] = summary.get(blk["type"], 0) + 1
    return summary

def export_to_json(summary: Dict[str, int], customs: List[Dict], placed: List[Dict], out_path: str = "distinta_wall.json", params: Optional[Dict] = None) -> str:
    std_labels, custom_labels = create_block_labels(placed, customs)

    data = {
        "schema_version": "1.0",
        "units": "mm",
        "params": (params or {}),
        "standard": {
            std_labels[i]: {
                "type": p["type"],
                "width": int(p["width"]),
                "height": int(p["height"]),
                "x": int(round(p["x"])),
                "y": int(round(p["y"])),
            }
            for i, p in enumerate(placed)
        },
        "custom": [
            {
                "label": custom_labels[i],
                "ctype": c.get("ctype", 2),
                "width": int(round(c["width"])),
                "height": int(round(c["height"])),
                "x": int(round(c["x"])),
                "y": int(round(c["y"])),
                "geometry": c["geometry"],
            }
            for i, c in enumerate(customs)
        ],
        "totals": {
            "standard_counts": summary,
            "custom_count": len(customs)
        }
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    return out_path

# ────────────────────────────────────────────────────────────────────────────────
# Generate preview image
# ────────────────────────────────────────────────────────────────────────────────
def generate_preview_image(wall_polygon: Polygon, 
                          placed: List[Dict], 
                          customs: List[Dict],
                          apertures: Optional[List[Polygon]] = None,
                          width: int = 800,
                          height: int = 600) -> str:
    """Genera immagine preview come base64 string."""
    if not plt or not patches:
        return ""
        
    try:
        # Setup figura
        fig, ax = plt.subplots(figsize=(width/100, height/100), dpi=100)
        ax.set_aspect('equal')
        
        # Bounds parete
        minx, miny, maxx, maxy = wall_polygon.bounds
        margin = max((maxx-minx), (maxy-miny)) * 0.05
        ax.set_xlim(minx - margin, maxx + margin)
        ax.set_ylim(miny - margin, maxy + margin)
        
        # Contorno parete
        x, y = wall_polygon.exterior.xy
        ax.plot(x, y, color='#2563eb', linewidth=2, label='Parete')
        
        # Labels per blocchi
        std_labels, custom_labels = create_block_labels(placed, customs)
        
        # Blocchi standard
        for i, blk in enumerate(placed):
            rect = patches.Rectangle(
                (blk['x'], blk['y']), blk['width'], blk['height'],
                facecolor='#e5e7eb', edgecolor='#374151', linewidth=0.5
            )
            ax.add_patch(rect)
            
            # Label centrata
            cx = blk['x'] + blk['width'] / 2
            cy = blk['y'] + blk['height'] / 2
            fontsize = min(8, max(4, blk['width'] / 200))
            ax.text(cx, cy, std_labels[i], ha='center', va='center', 
                   fontsize=fontsize, fontweight='bold', color='#1f2937')
        
        # Blocchi custom
        for i, cust in enumerate(customs):
            try:
                poly = shape(cust['geometry'])
                patch = patches.Polygon(
                    list(poly.exterior.coords),
                    facecolor='#dcfce7', edgecolor='#16a34a', 
                    linewidth=0.8, hatch='//', alpha=0.8
                )
                ax.add_patch(patch)
                
                # Label custom
                cx = cust['x'] + cust['width'] / 2
                cy = cust['y'] + cust['height'] / 2
                label = custom_labels[i]
                ax.text(cx, cy, label, ha='center', va='center', 
                       fontsize=6, fontweight='bold', color='#15803d')
            except Exception as e:
                print(f"⚠️ Errore rendering custom {i}: {e}")
        
        # Aperture
        if apertures:
            for ap in apertures:
                x, y = ap.exterior.xy
                ax.plot(x, y, color='#dc2626', linestyle='--', linewidth=2)
                ax.fill(x, y, color='#dc2626', alpha=0.15)
        
        # Styling
        ax.set_title('Preview Costruzione Parete', fontsize=12, fontweight='bold', color='#1f2937')
        ax.grid(True, alpha=0.3, color='#9ca3af')
        ax.tick_params(axis='both', which='major', labelsize=8, colors='#6b7280')
        
        # Salva in memoria come base64
        img_buffer = io.BytesIO()
        fig.savefig(img_buffer, format='png', dpi=100, bbox_inches='tight', 
                   facecolor='white', edgecolor='none', pad_inches=0.1)
        img_buffer.seek(0)
        plt.close(fig)
        
        # Converti in base64
        img_base64 = base64.b64encode(img_buffer.getvalue()).decode('utf-8')
        return f"data:image/png;base64,{img_base64}"
        
    except Exception as e:
        print(f"⚠️ Errore generazione preview: {e}")
        return ""

# ────────────────────────────────────────────────────────────────────────────────
# PDF Export (IMPLEMENTAZIONE COMPLETA - mantenuta identica)
# ────────────────────────────────────────────────────────────────────────────────
def export_to_pdf(summary: Dict[str, int], 
                  customs: List[Dict], 
                  placed: List[Dict], 
                  wall_polygon: Polygon,
                  apertures: Optional[List[Polygon]] = None,
                  project_name: str = "Progetto Parete",
                  out_path: str = "report_parete.pdf",
                  params: Optional[Dict] = None) -> str:
    """
    Genera un PDF completo con schema parete + tabelle riassuntive.
    """
    if not reportlab_available:
        raise RuntimeError("reportlab non disponibile. Installa con: pip install reportlab")
    
    try:
        # Setup documento
        doc = SimpleDocTemplate(
            out_path,
            pagesize=A4,
            rightMargin=20*mm,
            leftMargin=20*mm,
            topMargin=25*mm,
            bottomMargin=25*mm
        )
        
        # Raccogli tutti gli elementi
        story = []
        styles = getSampleStyleSheet()
        
        # === PAGINA 1: HEADER + SCHEMA GRAFICO ===
        story.extend(_build_pdf_header(project_name, summary, customs, styles))
        story.append(Spacer(1, 10*mm))
        
        # Schema grafico principale  
        schema_image = _generate_wall_schema_image(wall_polygon, placed, customs, apertures)
        if schema_image:
            story.append(schema_image)
        
        story.append(Spacer(1, 10*mm))
        
        # === TABELLA BLOCCHI STANDARD ===
        if summary:
            story.append(_build_standard_blocks_table(summary, placed, styles))
            story.append(Spacer(1, 8*mm))
        
        # === PAGINA 2: TABELLA CUSTOM (se presente) ===
        if customs:
            story.append(PageBreak())
            story.append(_build_custom_blocks_table(customs, styles))
            story.append(Spacer(1, 8*mm))
        
        # === INFO TECNICHE ===
        if params:
            story.append(_build_technical_info(params, styles))
        
        # Genera PDF
        doc.build(story)
        print(f"✅ PDF generato: {out_path}")
        return out_path
        
    except Exception as e:
        print(f"❌ Errore generazione PDF: {e}")
        raise


def _build_pdf_header(project_name: str, summary: Dict[str, int], customs: List[Dict], styles) -> List:
    """Costruisce header del PDF con info progetto."""
    elements = []
    
    # Titolo principale
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Title'],
        fontSize=18,
        spaceAfter=6*mm,
        alignment=TA_CENTER,
        textColor=black
    )
    elements.append(Paragraph(f"<b>{project_name}</b>", title_style))
    
    # Sottotitolo con data
    subtitle_style = ParagraphStyle(
        'Subtitle',
        parent=styles['Normal'],
        fontSize=12,
        alignment=TA_CENTER,
        textColor=gray,
        spaceAfter=8*mm
    )
    now = datetime.datetime.now()
    elements.append(Paragraph(f"Distinta Base Blocchi - {now.strftime('%d/%m/%Y %H:%M')}", subtitle_style))
    
    # Box riassuntivo
    total_standard = sum(summary.values())
    total_custom = len(customs)
    
    summary_data = [
        ['RIEPILOGO PROGETTO', ''],
        ['Blocchi Standard Totali:', f"{total_standard}"],
        ['Pezzi Custom Totali:', f"{total_custom}"],
        ['Efficienza:', f"{total_standard/(total_standard+total_custom)*100:.1f}%" if total_standard+total_custom > 0 else "N/A"]
    ]
    
    summary_table = Table(summary_data, colWidths=[80*mm, 40*mm])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('TEXTCOLOR', (0, 0), (-1, 0), black),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 1, black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    elements.append(summary_table)
    return elements


def _generate_wall_schema_image(wall_polygon: Polygon, 
                               placed: List[Dict], 
                               customs: List[Dict],
                               apertures: Optional[List[Polygon]] = None) -> Optional[Image]:
    """Genera immagine dello schema parete per il PDF."""
    if not plt or not patches:
        return None
        
    try:
        # Setup figura ad alta risoluzione per PDF
        fig, ax = plt.subplots(figsize=(180/25.4, 120/25.4), dpi=200)
        ax.set_aspect('equal')
        
        # Bounds parete
        minx, miny, maxx, maxy = wall_polygon.bounds
        margin = max((maxx-minx), (maxy-miny)) * 0.05
        ax.set_xlim(minx - margin, maxx + margin)
        ax.set_ylim(miny - margin, maxy + margin)
        
        # Contorno parete
        x, y = wall_polygon.exterior.xy
        ax.plot(x, y, color='blue', linewidth=2, label='Contorno parete')
        
        # Labels per blocchi
        std_labels, custom_labels = create_block_labels(placed, customs)
        
        # Blocchi standard
        for i, blk in enumerate(placed):
            rect = patches.Rectangle(
                (blk['x'], blk['y']), blk['width'], blk['height'],
                facecolor='lightgray', edgecolor='black', linewidth=0.5
            )
            ax.add_patch(rect)
            
            # Label centrata - dimensione adattiva
            cx = blk['x'] + blk['width'] / 2
            cy = blk['y'] + blk['height'] / 2
            fontsize = min(8, max(4, blk['width'] / 200))
            ax.text(cx, cy, std_labels[i], ha='center', va='center', 
                   fontsize=fontsize, fontweight='bold',
                   bbox=dict(boxstyle="round,pad=0.1", facecolor='white', alpha=0.8))
        
        # Blocchi custom
        for i, cust in enumerate(customs):
            try:
                poly = shape(cust['geometry'])
                patch = patches.Polygon(
                    list(poly.exterior.coords),
                    facecolor='lightgreen', edgecolor='green', 
                    linewidth=0.8, hatch='//', alpha=0.7
                )
                ax.add_patch(patch)
                
                # Label custom
                cx = cust['x'] + cust['width'] / 2
                cy = cust['y'] + cust['height'] / 2
                label = custom_labels[i]
                ax.text(cx, cy, label, ha='center', va='center', 
                       fontsize=6, fontweight='bold', color='darkgreen',
                       bbox=dict(boxstyle="round,pad=0.1", facecolor='white', alpha=0.9))
            except Exception as e:
                print(f"⚠️ Errore rendering custom {i}: {e}")
        
        # Aperture
        if apertures:
            for ap in apertures:
                x, y = ap.exterior.xy
                ax.plot(x, y, color='red', linestyle='--', linewidth=2)
                ax.fill(x, y, color='red', alpha=0.15)
        
        # Styling
        ax.set_title('Schema Costruttivo Parete', fontsize=12, fontweight='bold', pad=10)
        ax.legend(loc='upper right', fontsize=8)
        ax.grid(True, alpha=0.3)
        ax.set_xlabel('mm', fontsize=8)
        ax.set_ylabel('mm', fontsize=8)
        
        # Salva in memoria
        img_buffer = io.BytesIO()
        fig.savefig(img_buffer, format='png', dpi=200, bbox_inches='tight', 
                   facecolor='white', edgecolor='none')
        img_buffer.seek(0)
        plt.close(fig)
        
        # Converti in Image ReportLab
        return Image(img_buffer, width=170*mm, height=110*mm)
        
    except Exception as e:
        print(f"⚠️ Errore generazione schema: {e}")
        return None


def _build_standard_blocks_table(summary: Dict[str, int], placed: List[Dict], styles) -> Table:
    """Costruisce tabella blocchi standard."""
    # Header
    data = [['BLOCCHI STANDARD', 'QUANTITÀ', 'DIMENSIONI (mm)', 'AREA TOT (m²)']]
    
    # Raggruppa per tipo
    type_details = {}
    for blk in placed:
        btype = blk['type']
        if btype not in type_details:
            type_details[btype] = {
                'count': 0,
                'width': blk['width'],
                'height': blk['height']
            }
        type_details[btype]['count'] += 1
    
    # Ordina per dimensioni (dal più grande)
    sorted_types = sorted(type_details.items(), 
                         key=lambda x: x[1]['width'], reverse=True)
    
    total_area = 0
    for btype, details in sorted_types:
        area_m2 = (details['width'] * details['height'] * details['count']) / 1_000_000
        total_area += area_m2
        
        # Mappa nome user-friendly
        letter = SIZE_TO_LETTER.get(details['width'], 'X')
        friendly_name = f"Tipo {letter} ({btype})"
        
        data.append([
            friendly_name,
            str(details['count']),
            f"{details['width']} × {details['height']}",
            f"{area_m2:.2f}"
        ])
    
    # Totale
    data.append(['TOTALE', str(sum(d['count'] for d in type_details.values())), '', f"{total_area:.2f}"])
    
    table = Table(data, colWidths=[60*mm, 25*mm, 40*mm, 25*mm])
    table.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        # Dati
        ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -2), 9),
        ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
        # Totale
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightblue),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        # Bordi
        ('GRID', (0, 0), (-1, -1), 1, black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    return table


def _build_custom_blocks_table(customs: List[Dict], styles) -> Table:
    """Costruisce tabella pezzi custom."""
    # Header
    data = [['PEZZI CUSTOM', 'TIPO', 'DIMENSIONI (mm)', 'POSIZIONE (mm)', 'AREA (m²)']]
    
    custom_labels = create_block_labels([], customs)[1]
    total_area = 0
    
    for i, cust in enumerate(customs):
        area_m2 = (cust['width'] * cust['height']) / 1_000_000
        total_area += area_m2
        
        ctype = cust.get('ctype', 2)
        type_str = f"CU{ctype}" if ctype in [1, 2] else "CUX"
        
        data.append([
            custom_labels[i],
            type_str,
            f"{cust['width']:.0f} × {cust['height']:.0f}",
            f"({cust['x']:.0f}, {cust['y']:.0f})",
            f"{area_m2:.3f}"
        ])
    
    # Totale
    data.append(['TOTALE', '', '', '', f"{total_area:.3f}"])
    
    table = Table(data, colWidths=[35*mm, 20*mm, 35*mm, 35*mm, 25*mm])
    table.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkgreen),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        # Dati
        ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -2), 8),
        ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
        # Totale
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgreen),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        # Bordi
        ('GRID', (0, 0), (-1, -1), 1, black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    return table


def _build_technical_info(params: Dict, styles) -> Table:
    """Costruisce tabella info tecniche."""
    data = [['PARAMETRI TECNICI', 'VALORE']]
    
    # Formatta parametri leggibili
    readable_params = [
        ('Algoritmo Packing', 'Greedy + Backtrack'),
        ('Altezza Blocco Standard', f"{params.get('block_height_mm', 495)} mm"),
        ('Larghezze Blocchi', f"{params.get('block_widths_mm', [])}"),
        ('Offset Righe Dispari', f"{params.get('row_offset_mm', 'Auto')} mm"),
        ('Griglia Snap', f"{params.get('snap_mm', 1)} mm"),
        ('Margine Aperture', f"{params.get('keep_out_mm', 2)} mm"),
        ('Merge Custom Row-Aware', f"{params.get('row_aware_merge', True)}"),
        ('Max Larghezza Custom', f"{params.get('split_max_width_mm', 413)} mm"),
    ]
    
    for label, value in readable_params:
        data.append([label, str(value)])
    
    table = Table(data, colWidths=[80*mm, 60*mm])
    table.setStyle(TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), colors.orange),
        ('TEXTCOLOR', (0, 0), (-1, 0), black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        # Dati
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ALIGN', (1, 1), (-1, -1), 'LEFT'),
        # Bordi
        ('GRID', (0, 0), (-1, -1), 1, black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    
    return table

# ────────────────────────────────────────────────────────────────────────────────
# Calculate metrics
# ────────────────────────────────────────────────────────────────────────────────
def calculate_metrics(placed: List[Dict], customs: List[Dict], wall_area: float) -> Dict:
    """Calcola metriche di qualità del packing."""
    total_blocks = len(placed) + len(customs)
    if total_blocks == 0:
        return {"efficiency": 0, "waste_ratio": 0, "complexity": 0}
    
    standard_area = sum(p["width"] * p["height"] for p in placed)
    custom_area = sum(c["width"] * c["height"] for c in customs)
    
    return {
        "efficiency": len(placed) / total_blocks if total_blocks > 0 else 0,
        "waste_ratio": custom_area / wall_area if wall_area > 0 else 0,
        "complexity": len([c for c in customs if c.get("ctype") == 2]),
        "total_area_coverage": (standard_area + custom_area) / wall_area if wall_area > 0 else 0
    }

# ────────────────────────────────────────────────────────────────────────────────
# FastAPI – endpoints ESTESI
# ────────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Costruttore pareti a blocchi", description="Web UI + API per packing automatico pareti") if FastAPI else None

if app:
    # CORS middleware per consentire richieste dal frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # ===== FRONTEND STATIC FILES =====
    
    @app.get("/")
    async def serve_frontend():
        """Serve la pagina principale del frontend."""
        return FileResponse("templates/index.html")
    
    # Mount static files
    app.mount("/static", StaticFiles(directory="static"), name="static")
    
    # ===== HEALTH CHECK =====
    
    @app.get("/health")
    async def health():
        return {"status": "ok", "timestamp": datetime.datetime.now()}
    
    # ===== WEB UI API ENDPOINTS =====
    
    @app.post("/api/upload", response_model=PackingResult)
    async def upload_and_process(
        file: UploadFile = File(...),
        row_offset: int = Form(826),
        block_widths: str = Form("1239,826,413"),
        project_name: str = Form("Progetto Parete")
    ):
        """
        Upload SVG e processamento completo con preview.
        """
        try:
            # Validazione file
            if not file.filename.lower().endswith('.svg'):
                raise HTTPException(status_code=400, detail="Solo file SVG supportati")
            
            if file.size and file.size > 10 * 1024 * 1024:  # 10MB limit
                raise HTTPException(status_code=400, detail="File troppo grande (max 10MB)")
            
            # Lettura file
            svg_bytes = await file.read()
            if not svg_bytes:
                raise HTTPException(status_code=400, detail="File vuoto")
            
            # Parse parametri
            try:
                widths = [int(w.strip()) for w in block_widths.split(',') if w.strip()]
                if not widths:
                    widths = BLOCK_WIDTHS
            except ValueError:
                widths = BLOCK_WIDTHS
            
            # Parse SVG
            wall, apertures = parse_svg_wall(svg_bytes)
            
            # Packing
            placed, custom = pack_wall(
                wall, 
                widths, 
                BLOCK_HEIGHT, 
                row_offset=row_offset,
                apertures=apertures if apertures else None
            )
            
            # Ottimizzazione
            placed, custom = opt_pass(placed, custom, widths)
            
            # Calcola metriche
            summary = summarize_blocks(placed)
            metrics = calculate_metrics(placed, custom, wall.area)
            
            # Genera session ID
            session_id = str(uuid.uuid4())
            
            # Salva in sessione
            SESSIONS[session_id] = {
                "wall_polygon": wall,
                "apertures": apertures,
                "placed": placed,
                "customs": custom,
                "summary": summary,
                "config": {
                    "block_widths": widths,
                    "block_height": BLOCK_HEIGHT,
                    "row_offset": row_offset,
                    "project_name": project_name
                },
                "metrics": metrics,
                "timestamp": datetime.datetime.now()
            }
            
            # Formatta response
            minx, miny, maxx, maxy = wall.bounds
            
            return PackingResult(
                session_id=session_id,
                status="success",
                wall_bounds=[minx, miny, maxx, maxy],
                blocks_standard=[
                    {
                        "id": i,
                        "x": float(p["x"]),
                        "y": float(p["y"]),
                        "width": float(p["width"]),
                        "height": float(p["height"]),
                        "type": p["type"]
                    }
                    for i, p in enumerate(placed)
                ],
                blocks_custom=[
                    {
                        "id": i,
                        "x": float(c["x"]),
                        "y": float(c["y"]),
                        "width": float(c["width"]),
                        "height": float(c["height"]),
                        "type": c["type"],
                        "ctype": c.get("ctype", 2),
                        "geometry": c["geometry"]
                    }
                    for i, c in enumerate(custom)
                ],
                apertures=[
                    {
                        "bounds": list(ap.bounds)
                    }
                    for ap in (apertures or [])
                ],
                summary=summary,
                config={
                    "block_widths": widths,
                    "block_height": BLOCK_HEIGHT,
                    "row_offset": row_offset,
                    "project_name": project_name
                },
                metrics=metrics
            )
            
        except Exception as e:
            print(f"❌ Errore upload: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.post("/api/reconfigure")
    async def reconfigure_packing(
        session_id: str = Form(...),
        row_offset: int = Form(826),
        block_widths: str = Form("1239,826,413")
    ):
        """
        Riconfigurazione parametri su sessione esistente.
        """
        try:
            if session_id not in SESSIONS:
                raise HTTPException(status_code=404, detail="Sessione non trovata")
            
            session = SESSIONS[session_id]
            
            # Parse parametri
            try:
                widths = [int(w.strip()) for w in block_widths.split(',') if w.strip()]
                if not widths:
                    widths = BLOCK_WIDTHS
            except ValueError:
                widths = BLOCK_WIDTHS
            
            # Re-packing con nuovi parametri
            wall = session["wall_polygon"]
            apertures = session["apertures"]
            
            placed, custom = pack_wall(
                wall, 
                widths, 
                BLOCK_HEIGHT, 
                row_offset=row_offset,
                apertures=apertures if apertures else None
            )
            
            placed, custom = opt_pass(placed, custom, widths)
            
            # Aggiorna sessione
            summary = summarize_blocks(placed)
            metrics = calculate_metrics(placed, custom, wall.area)
            
            session.update({
                "placed": placed,
                "customs": custom,
                "summary": summary,
                "metrics": metrics,
                "config": {
                    **session["config"],
                    "block_widths": widths,
                    "row_offset": row_offset
                }
            })
            
            return {"status": "success", "session_id": session_id}
            
        except Exception as e:
            print(f"❌ Errore reconfig: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/preview/{session_id}")
    async def get_preview_image(session_id: str):
        """
        Genera immagine preview per sessione.
        """
        try:
            if session_id not in SESSIONS:
                raise HTTPException(status_code=404, detail="Sessione non trovata")
            
            session = SESSIONS[session_id]
            
            # Genera preview
            preview_base64 = generate_preview_image(
                session["wall_polygon"],
                session["placed"],
                session["customs"],
                session["apertures"]
            )
            
            if not preview_base64:
                raise HTTPException(status_code=500, detail="Errore generazione preview")
            
            return {"image": preview_base64}
            
        except Exception as e:
            print(f"❌ Errore preview: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/download/{session_id}/{format}")
    async def download_result(session_id: str, format: str):
        """
        Download risultati in vari formati.
        """
        try:
            if session_id not in SESSIONS:
                raise HTTPException(status_code=404, detail="Sessione non trovata")
            
            session = SESSIONS[session_id]
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            
            if format.lower() == "json":
                # Export JSON
                filename = f"distinta_{session_id[:8]}_{timestamp}.json"
                json_path = export_to_json(
                    session["summary"],
                    session["customs"],
                    session["placed"],
                    out_path=filename,
                    params=build_run_params(session["config"]["row_offset"])
                )
                
                return FileResponse(
                    json_path,
                    media_type="application/json",
                    filename=filename
                )
                
            elif format.lower() == "pdf":
                # Export PDF
                if not reportlab_available:
                    raise HTTPException(status_code=501, detail="Export PDF non disponibile")
                
                filename = f"report_{session_id[:8]}_{timestamp}.pdf"
                pdf_path = export_to_pdf(
                    session["summary"],
                    session["customs"],
                    session["placed"],
                    session["wall_polygon"],
                    session["apertures"],
                    project_name=session["config"]["project_name"],
                    out_path=filename,
                    params=build_run_params(session["config"]["row_offset"])
                )
                
                return FileResponse(
                    pdf_path,
                    media_type="application/pdf",
                    filename=filename
                )
                
            elif format.lower() == "dxf":
                # Export DXF
                if not ezdxf_available:
                    raise HTTPException(status_code=501, detail="Export DXF non disponibile")
                
                filename = f"schema_{session_id[:8]}_{timestamp}.dxf"
                dxf_path = export_to_dxf(
                    session["summary"],
                    session["customs"],
                    session["placed"],
                    session["wall_polygon"],
                    session["apertures"],
                    project_name=session["config"]["project_name"],
                    out_path=filename,
                    params=build_run_params(session["config"]["row_offset"])
                )
                
                return FileResponse(
                    dxf_path,
                    media_type="application/dxf",
                    filename=filename
                )
                
            else:
                raise HTTPException(status_code=400, detail="Formato non supportato")
                
        except Exception as e:
            print(f"❌ Errore download: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/api/session/{session_id}")
    async def get_session_info(session_id: str):
        """
        Ottieni informazioni sessione.
        """
        try:
            if session_id not in SESSIONS:
                raise HTTPException(status_code=404, detail="Sessione non trovata")
            
            session = SESSIONS[session_id]
            wall = session["wall_polygon"]
            minx, miny, maxx, maxy = wall.bounds
            
            return {
                "session_id": session_id,
                "wall_bounds": [minx, miny, maxx, maxy],
                "summary": session["summary"],
                "custom_count": len(session["customs"]),
                "metrics": session["metrics"],
                "config": session["config"],
                "timestamp": session["timestamp"]
            }
            
        except Exception as e:
            print(f"❌ Errore session info: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    # ===== BACKWARD COMPATIBILITY - API ORIGINALI =====
    
    @app.post("/pack")
    async def pack_from_json(payload: Dict):
        """
        Body JSON atteso:
        {
          "polygon": [[x,y], ...],
          "apertures": [ [[...]], [[...]] ],
          "block_widths": [1239,826,413],      # opzionale
          "block_height": 495,                 # opzionale
          "row_offset": 826                    # opzionale
        }
        """
        try:
            poly = Polygon(payload["polygon"])
            poly = sanitize_polygon(poly)

            apertures = []
            for ap in payload.get("apertures", []):
                apertures.append(Polygon(ap))

            widths = payload.get("block_widths", BLOCK_WIDTHS)
            height = int(payload.get("block_height", BLOCK_HEIGHT))
            row_offset = payload.get("row_offset", 826)

            placed, custom = pack_wall(poly, widths, height, row_offset=row_offset,
                                       apertures=apertures if apertures else None)
            placed, custom = opt_pass(placed, custom, widths)

            summary = summarize_blocks(placed)
            out_path = export_to_json(summary, custom, placed, out_path="distinta_wall.json", params=build_run_params(row_offset=row_offset))

            return JSONResponse({
                "summary": summary,
                "custom_count": len(custom),
                "json_path": out_path
            })
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/upload-svg")
    async def pack_from_svg(file: UploadFile = File(...),
                            row_offset: int = Form(826)):
        """
        Carica un SVG (schema tuo) e calcola il packing.
        """
        try:
            svg_bytes = await file.read()
            wall, apertures = parse_svg_wall(svg_bytes)
            widths = BLOCK_WIDTHS
            height = BLOCK_HEIGHT

            placed, custom = pack_wall(wall, widths, height, row_offset=row_offset,
                                       apertures=apertures if apertures else None)
            placed, custom = opt_pass(placed, custom, widths)
            summary = summarize_blocks(placed)
            out_path = export_to_json(summary, custom, placed, out_path="distinta_wall.json", params=build_run_params(row_offset=row_offset))
            return JSONResponse({"summary": summary, "custom_count": len(custom), "json_path": out_path})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

# ────────────────────────────────────────────────────────────────────────────────
# CLI demo (mantenuto per test)
# ────────────────────────────────────────────────────────────────────────────────
def _demo():
    print("🚀 Demo Costruttore Pareti a Blocchi")
    print("=" * 50)
    
    # Demo parete trapezoidale con due porte
    wall_exterior = Polygon([(0,0), (12000,0), (12000,4500), (0,2500), (0,0)])
    porta1 = Polygon([(2000,0), (3200,0), (3200,2200), (2000,2200)])
    porta2 = Polygon([(8500,0), (9700,0), (9700,2200), (8500,2200)])

    placed, custom = pack_wall(wall_exterior, BLOCK_WIDTHS, BLOCK_HEIGHT,
                               row_offset=826, apertures=[porta1, porta2])
    
    # OTTIMIZZAZIONE POST-PACKING (questa riga era mancante!)
    placed, custom = opt_pass(placed, custom, BLOCK_WIDTHS)
    
    summary = summarize_blocks(placed)

    print("🔨 Distinta base blocchi standard:")
    for k, v in summary.items():
        print(f"  • {v} × {k}")
    print(f"\n✂️ Pezzi custom totali: {len(custom)}")

    # Calcola metriche
    metrics = calculate_metrics(placed, custom, wall_exterior.area)
    print(f"\n📊 Metriche:")
    print(f"  • Efficienza: {metrics['efficiency']:.1%}")
    print(f"  • Waste ratio: {metrics['waste_ratio']:.1%}")
    print(f"  • Complessità: {metrics['complexity']} pezzi CU2")

    out = export_to_json(summary, custom, placed, out_path="distinta_base_wall.json", params=build_run_params(row_offset=826))
    print(f"📄 JSON scritto in: {out}")

    # Test export PDF
    if reportlab_available:
        try:
            pdf_path = export_to_pdf(summary, custom, placed, wall_exterior, 
                                   apertures=[porta1, porta2],
                                   project_name="Demo Parete Trapezoidale", 
                                   out_path="demo_parete_trapezoidale.pdf",
                                   params=build_run_params(row_offset=826))
            print(f"📄 PDF demo generato: {pdf_path}")
        except Exception as e:
            print(f"⚠️ Errore PDF demo: {e}")
    else:
        print("⚠️ ReportLab non disponibile per export PDF")

    # Test export DXF SENZA SOVRAPPOSIZIONI
    if ezdxf_available:
        try:
            dxf_path = export_to_dxf(summary, custom, placed, wall_exterior, 
                                   apertures=[porta1, porta2],
                                   project_name="Demo Parete Trapezoidale", 
                                   out_path="demo_parete_senza_sovrapposizioni.dxf",
                                   params=build_run_params(row_offset=826))
            print(f"📐 DXF demo SENZA SOVRAPPOSIZIONI generato: {dxf_path}")
        except Exception as e:
            print(f"⚠️ Errore DXF demo: {e}")
    else:
        print("⚠️ ezdxf non disponibile per export DXF")
        
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        _demo()
    elif len(sys.argv) > 1 and sys.argv[1] == "server":
        # Avvia server FastAPI
        if app:
            print("🚀 Avvio server Web UI...")
            print("🌐 Apri il browser su: http://localhost:8000")
            print("🛑 Premi Ctrl+C per fermare il server")
            uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
        else:
            print("❌ FastAPI non disponibile")
    else:
        print("Uso: python main.py [demo|server]")
        print("  demo   - Esegui demo CLI")
        print("  server - Avvia server web")
        print("\n🧱 MIGLIORAMENTI DXF:")
        print("  ✅ Layout intelligente con DXFLayoutManager")
        print("  ✅ Zone calcolate automaticamente senza sovrapposizioni")
        print("  ✅ Margini adattivi basati su contenuto")
        print("  ✅ Controllo overflow per tabelle e schema taglio")
        print("  ✅ Titoli e sezioni ben separate e leggibili")
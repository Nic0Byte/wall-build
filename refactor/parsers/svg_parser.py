"""
🔄 PARSERS REFACTOR - FASE 1: COPY-FIRST

Questo modulo COPIA (non sposta) le funzioni di parsing da main.py
mantenendo la compatibilità al 100%.

REGOLA: main.py NON viene modificato in questa fase.
"""

# Re-import di tutte le dipendenze necessarie dai parsers di main.py
import io
import os
import math
import xml.etree.ElementTree as ET
import re
import tempfile
from typing import List, Tuple, Dict, Optional

from shapely.geometry import Polygon, MultiPolygon, LinearRing, box
from shapely.ops import unary_union

# Re-import delle configurazioni (per ora copia)
try:
    from utils.geometry_utils import sanitize_polygon, SNAP_MM
    from utils.config import AREA_EPS
except ImportError:
    # Fallback se utils non disponibile
    SNAP_MM = 1.0
    AREA_EPS = 1e-3
    
    def sanitize_polygon(poly):
        """Fallback sanitize_polygon."""
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly

# Optional dependencies (come in main.py)
try:
    import svgpathtools  # type: ignore
except Exception:
    svgpathtools = None

try:
    import ezdxf
    ezdxf_available = True
except ImportError:
    ezdxf_available = False

try:
    import dxfgrabber
    dxfgrabber_available = True
except ImportError:
    dxfgrabber_available = False


# ────────────────────────────────────────────────────────────────────────────────
# COPIATO DA main.py: SVG PARSING FUNCTIONS 
# ────────────────────────────────────────────────────────────────────────────────

def parse_svg_wall(svg_bytes: bytes, layer_wall: str = "MURO", layer_holes: str = "BUCHI") -> Tuple[Polygon, List[Polygon]]:
    """
    Parser SVG reale che estrae parete e aperture dai layer specificati.
    
    COPIATO IDENTICAMENTE DA main.py per garantire compatibilità.
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
        
        print(f" SVG parsed: parete {wall_polygon.area:.1f} mm², {len(aperture_polygons)} aperture")
        return wall_polygon, aperture_polygons
        
    except Exception as e:
        print(f" Errore parsing SVG: {e}")
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
        print(" Impossibile determinare scala, usando 1:1")
        return 1.0


def _extract_geometries_by_layer(root: ET.Element, ns: Dict[str, str], layer_name: str, scale: float) -> List[List[Tuple[float, float]]]:
    """Estrae tutte le geometrie dal layer specificato."""
    geometries = []
    
    # Cerca group con id/inkscape:label che corrisponde al layer
    for group in root.findall('.//svg:g', ns):
        group_id = group.get('id', '')
        group_label = group.get('{http://www.inkscape.org/namespaces/inkscape}label', '')
        group_class = group.get('class', '')
        
        # Verifica match con diversi formati
        layer_match = (
            layer_name.lower() in group_id.lower() or 
            layer_name.lower() in group_label.lower() or
            layer_name.lower() in group_class.lower() or
            f"layer_{layer_name.lower()}" == group_id.lower() or
            f"layer-{layer_name.lower()}" in group_class.lower()
        )
        
        if layer_match:
            print(f" Trovato layer '{layer_name}' nel gruppo: {group_id}")
            geometries.extend(_extract_paths_from_group(group, ns, scale))
            
    # Se non trova layer specifici, cerca elementi top-level
    if not geometries:
        print(f" Layer '{layer_name}' non trovato, cercando geometrie generiche...")
        geometries.extend(_extract_paths_from_group(root, ns, scale))
    
    return geometries


def _extract_paths_from_group(group: ET.Element, ns: Dict[str, str], scale: float) -> List[List[Tuple[float, float]]]:
    """Estrae path, rect, circle, polygon da un gruppo SVG."""
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
                print(f" Errore parsing path: {e}")
    
    # Polygon elements (aggiunti per i nostri SVG convertiti)
    for polygon in group.findall('.//svg:polygon', ns):
        points = polygon.get('points')
        if points:
            try:
                coords = _parse_svg_polygon_points(points, scale)
                if coords and len(coords) >= 3:
                    geometries.append(coords)
                    print(f" Polygon trovato: {len(coords)} punti")
            except Exception as e:
                print(f" Errore parsing polygon: {e}")
    
    # Polyline elements
    for polyline in group.findall('.//svg:polyline', ns):
        points = polyline.get('points')
        if points:
            try:
                coords = _parse_svg_polygon_points(points, scale)
                if coords and len(coords) >= 2:
                    geometries.append(coords)
                    print(f" Polyline trovata: {len(coords)} punti")
            except Exception as e:
                print(f" Errore parsing polyline: {e}")
    
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
            print(f" Errore parsing rect: {e}")
    
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
            print(f" Errore parsing circle: {e}")
    
    return geometries


# Le altre funzioni _parse_svg_* saranno aggiunte nella prossima iterazione
# per mantenere il commit ragionevolmente piccolo

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
        print(f" svgpathtools fallito: {e}")
    
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


def _parse_svg_polygon_points(points_data: str, scale: float) -> List[Tuple[float, float]]:
    """Parser per attributo 'points' di polygon/polyline SVG."""
    coords = []
    
    try:
        # Rimuovi virgole extra e normalizza spazi
        normalized = points_data.replace(',', ' ').strip()
        
        # Estrai tutti i numeri
        numbers = re.findall(r'-?[\d.]+', normalized)
        
        # Raggruppa in coppie x,y
        for i in range(0, len(numbers) - 1, 2):
            x = float(numbers[i]) * scale
            y = float(numbers[i + 1]) * scale
            coords.append((x, y))
        
        # Assicurati che il primo e ultimo punto siano uguali per chiudere
        if len(coords) > 2 and coords[0] != coords[-1]:
            coords.append(coords[0])
            
    except Exception as e:
        print(f" Errore parsing points '{points_data}': {e}")
    
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
            print(f" Geometria scartata: {e}")
    
    if not valid_polygons:
        raise ValueError("Nessuna geometria valida trovata")
    
    # Se è una parete, prendi l'unione o il poligono più grande
    if is_wall:
        if len(valid_polygons) == 1:
            return valid_polygons[0]
        else:
            # Prendi il poligono più grande come parete principale
            largest = max(valid_polygons, key=lambda p: p.area)
            print(f" Trovati {len(valid_polygons)} poligoni, usando il più grande")
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
            print(f" Apertura scartata: {e}")
    
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
        
        print(f" Fallback parse: parete {wall.area:.1f} mm², {len(apertures)} aperture")
        return wall, apertures
        
    except Exception as e:
        print(f" Anche il fallback è fallito: {e}")
        # Ultimo fallback: parete rettangolare di esempio
        wall = Polygon([(0, 0), (5000, 0), (5000, 3000), (0, 3000)])
        return wall, []


# ────────────────────────────────────────────────────────────────────────────────
# ENTRY POINT FUNCTIONS
# ────────────────────────────────────────────────────────────────────────────────

def test_svg_parsing():
    """Test rapido del parsing SVG estratto."""
    # Mini SVG di test
    test_svg = b'''<?xml version="1.0"?>
    <svg xmlns="http://www.w3.org/2000/svg" width="1000" height="500">
        <polygon points="0,0 1000,0 1000,500 0,500" fill="none" stroke="black"/>
    </svg>'''
    
    try:
        wall, apertures = parse_svg_wall(test_svg)
        print(f"✅ Test SVG parsing: parete {wall.area:.1f} mm², {len(apertures)} aperture")
        return True
    except Exception as e:
        print(f"❌ Test SVG parsing fallito: {e}")
        return False


if __name__ == "__main__":
    print("🔄 PARSERS REFACTOR - TEST ESTRATTO")
    print("=" * 40)
    
    test_svg_parsing()
    
    print(f"📦 Modulo parsers estratto con {globals().keys().__len__()} funzioni")
    print("✅ Pronto per test di compatibilità")
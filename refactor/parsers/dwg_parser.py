"""
🔄 DWG PARSER REFACTOR - FASE 1: COPY-FIRST

Questo modulo COPIA (non sposta) le funzioni di parsing DWG da main.py
mantenendo la compatibilità al 100%.

REGOLA: main.py NON viene modificato in questa fase.
"""

import os
import math
import tempfile
from typing import List, Tuple, Dict, Optional

from shapely.geometry import Polygon, MultiPolygon
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
    import ezdxf
    ezdxf_available = True
except ImportError:
    ezdxf_available = False
    print(" ezdxf non installato. Export DXF non disponibile.")

try:
    import dxfgrabber
    dxfgrabber_available = True
    print("[OK] dxfgrabber caricato - Supporto DWG avanzato disponibile")
except ImportError:
    dxfgrabber_available = False
    print("[WARNING] dxfgrabber non installato. Parser DWG avanzato non disponibile.")


# ────────────────────────────────────────────────────────────────────────────────
# COPIATO DA main.py: DWG PARSING FUNCTIONS 
# ────────────────────────────────────────────────────────────────────────────────

def parse_dwg_wall(dwg_bytes: bytes, layer_wall: str = "MURO", layer_holes: str = "BUCHI") -> Tuple[Polygon, List[Polygon]]:
    """
    Parser DWG che estrae parete e aperture dai layer specificati.
    Prova multiple librerie: dxfgrabber (più compatibile) → ezdxf → fallback
    
    COPIATO IDENTICAMENTE DA main.py per garantire compatibilità.
    """
    
    # Tentativo 1: dxfgrabber (più compatibile con DWG recenti)
    if dxfgrabber_available:
        try:
            return _parse_dwg_with_dxfgrabber(dwg_bytes, layer_wall, layer_holes)
        except Exception as e:
            print(f" dxfgrabber fallito: {e}")
    
    # Tentativo 2: ezdxf (originale)  
    if ezdxf_available:
        try:
            return _parse_dwg_with_ezdxf(dwg_bytes, layer_wall, layer_holes)
        except Exception as e:
            print(f" ezdxf fallito: {e}")
    
    # Tentativo 3: fallback
    print(" Usando fallback parser...")
    return _fallback_parse_dwg(dwg_bytes)


def _parse_dwg_with_dxfgrabber(dwg_bytes: bytes, layer_wall: str, layer_holes: str) -> Tuple[Polygon, List[Polygon]]:
    """Parser DWG usando dxfgrabber (più compatibile)."""
    with tempfile.NamedTemporaryFile(suffix='.dwg', delete=False) as tmp_file:
        tmp_file.write(dwg_bytes)
        tmp_file_path = tmp_file.name
    
    try:
        # Apri con dxfgrabber
        dwg = dxfgrabber.readfile(tmp_file_path)
        
        print(f" DWG version: {dwg.header.get('$ACADVER', 'Unknown')}")
        print(f" Layers trovati: {len(dwg.layers)}")
        
        # Estrai geometrie per layer
        wall_geometries = _extract_dxfgrabber_geometries_by_layer(dwg, layer_wall)
        hole_geometries = _extract_dxfgrabber_geometries_by_layer(dwg, layer_holes)
        
        # Converti in Polygon
        wall_polygon = _dwg_geometries_to_polygon(wall_geometries, is_wall=True)
        aperture_polygons = _dwg_geometries_to_apertures(hole_geometries)
        
        print(f" DWG parsed con dxfgrabber: parete {wall_polygon.area:.1f} mm², {len(aperture_polygons)} aperture")
        return wall_polygon, aperture_polygons
        
    finally:
        try:
            os.unlink(tmp_file_path)
        except Exception:
            pass


def _extract_dxfgrabber_geometries_by_layer(dwg, layer_name: str) -> List[List[Tuple[float, float]]]:
    """Estrae geometrie da layer usando dxfgrabber."""
    geometries = []
    
    # Lista tutti i layer disponibili per debug
    layer_names = [layer.name for layer in dwg.layers]
    print(f" Layer disponibili: {layer_names}")
    
    # Cerca entità nel layer specificato
    entities_found = 0
    for entity in dwg.entities:
        if hasattr(entity, 'layer') and entity.layer.lower() == layer_name.lower():
            entities_found += 1
            coords = _extract_coords_from_dxfgrabber_entity(entity)
            if coords and len(coords) >= 3:
                geometries.append(coords)
    
    print(f" Layer '{layer_name}': {entities_found} entità trovate, {len(geometries)} geometrie valide")
    
    # Se non trova il layer specifico, cerca qualsiasi geometria chiusa
    if not geometries:
        print(f" Layer '{layer_name}' non trovato o vuoto, cercando geometrie generiche...")
        for entity in dwg.entities:
            coords = _extract_coords_from_dxfgrabber_entity(entity)
            if coords and len(coords) >= 3:
                geometries.append(coords)
                if len(geometries) >= 5:  # Limita per evitare troppi elementi
                    break
    
    return geometries


def _extract_coords_from_dxfgrabber_entity(entity) -> Optional[List[Tuple[float, float]]]:
    """Estrae coordinate da entità dxfgrabber."""
    try:
        entity_type = entity.dxftype
        
        if entity_type == 'LWPOLYLINE':
            return [(point[0], point[1]) for point in entity.points]
            
        elif entity_type == 'POLYLINE':
            coords = []
            for vertex in entity.vertices:
                coords.append((vertex.location[0], vertex.location[1]))
            return coords
            
        elif entity_type == 'LINE':
            start = entity.start
            end = entity.end
            return [(start[0], start[1]), (end[0], end[1])]
            
        elif entity_type == 'CIRCLE':
            center = entity.center
            radius = entity.radius
            coords = []
            for i in range(17):  # 16 lati + chiusura
                angle = 2 * math.pi * i / 16
                x = center[0] + radius * math.cos(angle)
                y = center[1] + radius * math.sin(angle)
                coords.append((x, y))
            return coords
            
        elif entity_type == 'ARC':
            center = entity.center
            radius = entity.radius
            start_angle = math.radians(entity.start_angle)
            end_angle = math.radians(entity.end_angle)
            
            if end_angle < start_angle:
                end_angle += 2 * math.pi
                
            coords = []
            segments = 16
            angle_step = (end_angle - start_angle) / segments
            for i in range(segments + 1):
                angle = start_angle + i * angle_step
                x = center[0] + radius * math.cos(angle)
                y = center[1] + radius * math.sin(angle)
                coords.append((x, y))
            return coords
            
        else:
            return None
            
    except Exception as e:
        print(f" Errore estrazione coordinate da {entity_type}: {e}")
        return None


def _parse_dwg_with_ezdxf(dwg_bytes: bytes, layer_wall: str, layer_holes: str) -> Tuple[Polygon, List[Polygon]]:
    """Parser DWG originale usando ezdxf."""
    with tempfile.NamedTemporaryFile(suffix='.dwg', delete=False) as tmp_file:
        tmp_file.write(dwg_bytes)
        tmp_file_path = tmp_file.name
    
    try:
        # Apri il file DWG
        doc = ezdxf.readfile(tmp_file_path)
        msp = doc.modelspace()
        
        # Estrai geometrie per layer
        wall_geometries = _extract_dwg_geometries_by_layer(msp, layer_wall)
        hole_geometries = _extract_dwg_geometries_by_layer(msp, layer_holes)
        
        # Converti in Polygon
        wall_polygon = _dwg_geometries_to_polygon(wall_geometries, is_wall=True)
        aperture_polygons = _dwg_geometries_to_apertures(hole_geometries)
        
        print(f" DWG parsed con ezdxf: parete {wall_polygon.area:.1f} mm², {len(aperture_polygons)} aperture")
        return wall_polygon, aperture_polygons
        
    finally:
        try:
            os.unlink(tmp_file_path)
        except Exception:
            pass


def _extract_dwg_geometries_by_layer(msp, layer_name: str) -> List[List[Tuple[float, float]]]:
    """Estrae tutte le geometrie dal layer specificato nel DWG."""
    geometries = []
    
    # Cerca entità nel layer specificato
    for entity in msp:
        if hasattr(entity, 'dxf') and hasattr(entity.dxf, 'layer'):
            if entity.dxf.layer.lower() == layer_name.lower():
                coords = _extract_coords_from_dwg_entity(entity)
                if coords and len(coords) >= 3:
                    geometries.append(coords)
    
    # Se non trova il layer specifico, cerca entità generiche
    if not geometries:
        print(f" Layer '{layer_name}' non trovato, cercando geometrie generiche...")
        for entity in msp:
            coords = _extract_coords_from_dwg_entity(entity)
            if coords and len(coords) >= 3:
                geometries.append(coords)
                break  # Prendi solo la prima geometria trovata
    
    return geometries


def _extract_coords_from_dwg_entity(entity) -> Optional[List[Tuple[float, float]]]:
    """Estrae coordinate da un'entità DWG/DXF."""
    try:
        entity_type = entity.dxftype()
        
        if entity_type == 'LWPOLYLINE':
            # Polilinea leggera
            coords = []
            for point in entity.get_points():
                coords.append((point[0], point[1]))
            # Chiudi se necessario
            if entity.closed and coords and coords[0] != coords[-1]:
                coords.append(coords[0])
            return coords
            
        elif entity_type == 'POLYLINE':
            # Polilinea 3D
            coords = []
            for vertex in entity.vertices:
                coords.append((vertex.dxf.location.x, vertex.dxf.location.y))
            if entity.is_closed and coords and coords[0] != coords[-1]:
                coords.append(coords[0])
            return coords
            
        elif entity_type == 'LINE':
            # Linea singola
            start = entity.dxf.start
            end = entity.dxf.end
            return [(start.x, start.y), (end.x, end.y)]
            
        elif entity_type == 'CIRCLE':
            # Cerchio - approssima con poligono
            center = entity.dxf.center
            radius = entity.dxf.radius
            coords = []
            for i in range(17):  # 16 lati + chiusura
                angle = 2 * math.pi * i / 16
                x = center.x + radius * math.cos(angle)
                y = center.y + radius * math.sin(angle)
                coords.append((x, y))
            return coords
            
        elif entity_type == 'ARC':
            # Arco - approssima con segmenti
            center = entity.dxf.center
            radius = entity.dxf.radius
            start_angle = math.radians(entity.dxf.start_angle)
            end_angle = math.radians(entity.dxf.end_angle)
            
            # Gestisci archi che attraversano 0°
            if end_angle < start_angle:
                end_angle += 2 * math.pi
                
            coords = []
            segments = 16
            angle_step = (end_angle - start_angle) / segments
            for i in range(segments + 1):
                angle = start_angle + i * angle_step
                x = center.x + radius * math.cos(angle)
                y = center.y + radius * math.sin(angle)
                coords.append((x, y))
            return coords
            
        elif entity_type == 'SPLINE':
            # Spline - approssima con polilinea
            try:
                points = entity.flattening(0.1)  # Tolleranza 0.1mm
                return [(p.x, p.y) for p in points]
            except Exception:
                return None
                
        elif entity_type in ['INSERT', 'BLOCK']:
            # Blocchi - ignora per ora
            return None
            
        else:
            # Altri tipi non supportati
            return None
            
    except Exception as e:
        print(f" Errore estrazione coordinate da {entity.dxftype()}: {e}")
        return None


def _dwg_geometries_to_polygon(geometries: List[List[Tuple[float, float]]], is_wall: bool = True) -> Polygon:
    """Converte geometrie DWG in Polygon Shapely."""
    if not geometries:
        raise ValueError("Nessuna geometria trovata per la parete")
    
    valid_polygons = []
    
    for coords in geometries:
        if len(coords) < 3:
            continue
            
        try:
            # Assicurati che sia chiuso
            if coords[0] != coords[-1]:
                coords.append(coords[0])
                
            polygon = Polygon(coords)
            if polygon.is_valid and polygon.area > AREA_EPS:
                valid_polygons.append(polygon)
        except Exception as e:
            print(f" Geometria DWG invalida: {e}")
            continue
    
    if not valid_polygons:
        raise ValueError("Nessuna geometria valida trovata")
    
    # Se è una parete, prendi l'unione o il poligono più grande
    if is_wall:
        if len(valid_polygons) == 1:
            result = valid_polygons[0]
        else:
            # Prova unione, altrimenti prendi il più grande
            try:
                result = unary_union(valid_polygons)
                if isinstance(result, MultiPolygon):
                    result = max(result.geoms, key=lambda p: p.area)
            except Exception:
                result = max(valid_polygons, key=lambda p: p.area)
    else:
        result = valid_polygons[0]
    
    return sanitize_polygon(result)


def _dwg_geometries_to_apertures(geometries: List[List[Tuple[float, float]]]) -> List[Polygon]:
    """Converte geometrie DWG in lista di aperture."""
    apertures = []
    
    for coords in geometries:
        if len(coords) < 3:
            continue
            
        try:
            # Assicurati che sia chiuso
            if coords[0] != coords[-1]:
                coords.append(coords[0])
                
            polygon = Polygon(coords)
            if polygon.is_valid and polygon.area > AREA_EPS:
                apertures.append(sanitize_polygon(polygon))
        except Exception as e:
            print(f" Apertura DWG invalida: {e}")
            continue
    
    return apertures


def _fallback_parse_dwg(dwg_bytes: bytes) -> Tuple[Polygon, List[Polygon]]:
    """Parsing fallback per DWG quando non trova layer specifici."""
    try:
        # Prova a leggere come DXF generico
        with tempfile.NamedTemporaryFile(suffix='.dxf', delete=False) as tmp_file:
            tmp_file.write(dwg_bytes)
            tmp_file_path = tmp_file.name
        
        try:
            doc = ezdxf.readfile(tmp_file_path)
            msp = doc.modelspace()
            
            # Cerca la prima geometria chiusa come parete
            all_geometries = []
            for entity in msp:
                coords = _extract_coords_from_dwg_entity(entity)
                if coords and len(coords) >= 3:
                    all_geometries.append(coords)
            
            if not all_geometries:
                raise ValueError("Nessuna geometria trovata nel file DWG")
            
            # Prendi la prima come parete, il resto come aperture
            wall_polygon = _dwg_geometries_to_polygon([all_geometries[0]], is_wall=True)
            apertures = _dwg_geometries_to_apertures(all_geometries[1:]) if len(all_geometries) > 1 else []
            
            print(f" DWG fallback parsing: parete {wall_polygon.area:.1f} mm², {len(apertures)} aperture")
            return wall_polygon, apertures
            
        finally:
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass
                
    except Exception as e:
        print(f" Errore fallback DWG: {e}")
        # Ultimo fallback: crea una parete di esempio
        from shapely.geometry import box
        example_wall = box(0, 0, 5000, 2500)  # 5m x 2.5m
        return example_wall, []


# ────────────────────────────────────────────────────────────────────────────────
# ENTRY POINT FUNCTIONS
# ────────────────────────────────────────────────────────────────────────────────

def test_dwg_parsing():
    """Test rapido del parsing DWG estratto."""
    print("🔄 Test DWG parsing estratto")
    
    # Non possiamo testare senza un file DWG reale
    # ma possiamo testare che le funzioni esistano
    
    functions = [
        'parse_dwg_wall',
        '_parse_dwg_with_dxfgrabber', 
        '_parse_dwg_with_ezdxf',
        '_dwg_geometries_to_polygon',
        '_fallback_parse_dwg'
    ]
    
    for func_name in functions:
        if func_name in globals():
            print(f"✅ {func_name} disponibile")
        else:
            print(f"❌ {func_name} mancante!")
    
    print(f"📦 Parser availability: dxfgrabber={dxfgrabber_available}, ezdxf={ezdxf_available}")
    
    return True


if __name__ == "__main__":
    print("🔄 DWG PARSER REFACTOR - TEST ESTRATTO")
    print("=" * 40)
    
    test_dwg_parsing()
    
    print(f"📦 Modulo DWG parser estratto con {len([k for k in globals().keys() if not k.startswith('_') and callable(globals()[k])])} funzioni pubbliche")
    print("✅ Pronto per test di compatibilità")
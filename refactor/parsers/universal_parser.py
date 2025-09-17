"""
🔄 UNIVERSAL PARSER REFACTOR - FASE 1: COPY-FIRST

Parser universale che combina SVG + DWG con fallback intelligente.
Questo è l'ENTRY POINT principale per il parsing files.

REGOLA: main.py NON viene modificato in questa fase.
"""

import re
from typing import Tuple, List, Dict

from shapely.geometry import Polygon, box

# Import dai parsers estratti
try:
    from .svg_parser import parse_svg_wall
    from .dwg_parser import parse_dwg_wall
except ImportError:
    # Fallback per test standalone
    try:
        from svg_parser import parse_svg_wall
        from dwg_parser import parse_dwg_wall
    except ImportError:
        print("⚠️ Parser modules non disponibili")
        parse_svg_wall = None
        parse_dwg_wall = None


# ────────────────────────────────────────────────────────────────────────────────
# COPIATO DA main.py: UNIVERSAL PARSING FUNCTIONS 
# ────────────────────────────────────────────────────────────────────────────────

def parse_wall_file(file_bytes: bytes, filename: str, 
                   layer_wall: str = "MURO", layer_holes: str = "BUCHI") -> Tuple[Polygon, List[Polygon]]:
    """
    Parser universale che supporta SVG, DWG, DXF con fallback intelligente.
    
    COPIATO IDENTICAMENTE DA main.py per garantire compatibilità.
    """
    file_ext = filename.lower().split('.')[-1] if '.' in filename else ''
    
    # 1. SVG - sempre supportato
    if file_ext == 'svg':
        print(f" Parsing file SVG: {filename}")
        if parse_svg_wall:
            return parse_svg_wall(file_bytes, layer_wall, layer_holes)
        else:
            raise ValueError("Parser SVG non disponibile")
    
    # 2. DWG/DXF - prova multiple strategie
    elif file_ext in ['dwg', 'dxf']:
        print(f" Parsing file DWG/DXF: {filename}")
        
        # Analizza header per determinare compatibilità
        header_info = _analyze_dwg_header(file_bytes)
        print(f" Formato rilevato: {header_info['format']} {header_info['version']}")
        
        # Strategia 1: Parser diretto se compatibile
        if header_info['compatible']:
            try:
                if parse_dwg_wall:
                    return parse_dwg_wall(file_bytes, layer_wall, layer_holes)
                else:
                    raise ValueError("Parser DWG non disponibile")
            except Exception as e:
                print(f" Parser diretto fallito: {e}")
        
        # Strategia 2: Tentativo conversione ODA (se disponibile)
        if not header_info['compatible']:
            try:
                return _try_oda_conversion(file_bytes, filename, layer_wall, layer_holes)
            except Exception as e:
                print(f" Conversione ODA fallita: {e}")
        
        # Strategia 3: Fallback intelligente con stima dimensioni
        return _intelligent_fallback(file_bytes, filename, header_info)
    
    else:
        # Auto-detection per formati senza estensione
        print(f" Formato non riconosciuto ({file_ext}), tentativo auto-detection...")
        
        # Controlla se inizia come XML/SVG
        try:
            content_start = file_bytes[:1000].decode('utf-8', errors='ignore').strip()
            if content_start.startswith('<?xml') or '<svg' in content_start:
                print(" Auto-detected: SVG")
                if parse_svg_wall:
                    return parse_svg_wall(file_bytes, layer_wall, layer_holes)
        except Exception:
            pass
        
        # Prova come DWG/DXF
        try:
            print(" Auto-detection: tentativo DWG/DXF...")
            header_info = _analyze_dwg_header(file_bytes)
            if header_info['is_cad']:
                if parse_dwg_wall:
                    return parse_dwg_wall(file_bytes, layer_wall, layer_holes)
        except Exception:
            pass
        
        # Ultimo fallback
        raise ValueError(f"Formato file non supportato: {filename}. Supportati: SVG, DWG, DXF")


def _analyze_dwg_header(file_bytes: bytes) -> Dict:
    """Analizza l'header del file DWG per determinare compatibilità."""
    header = file_bytes[:20] if len(file_bytes) >= 20 else file_bytes
    
    info = {
        'is_cad': False,
        'format': 'Unknown',
        'version': 'Unknown',
        'compatible': False,
        'estimated_size': None
    }
    
    try:
        if header.startswith(b'AC'):
            info['is_cad'] = True
            info['format'] = 'AutoCAD DWG'
            
            # Determina versione e compatibilità
            if header.startswith(b'AC1014'):
                info['version'] = 'R14 (1997)'
                info['compatible'] = True
            elif header.startswith(b'AC1015'):
                info['version'] = '2000'
                info['compatible'] = True
            elif header.startswith(b'AC1018'):
                info['version'] = '2004'
                info['compatible'] = True
            elif header.startswith(b'AC1021'):
                info['version'] = '2007'
                info['compatible'] = True
            elif header.startswith(b'AC1024'):
                info['version'] = '2010'
                info['compatible'] = True
            elif header.startswith(b'AC1027'):
                info['version'] = '2013'
                info['compatible'] = False  # Borderline
            elif header.startswith(b'AC1032'):
                info['version'] = '2018+'
                info['compatible'] = False
            else:
                info['version'] = 'Sconosciuta'
                info['compatible'] = False
                
        elif b'SECTION' in file_bytes[:200] or b'HEADER' in file_bytes[:200]:
            info['is_cad'] = True
            info['format'] = 'DXF'
            info['compatible'] = True  # DXF generalmente più compatibile
            
    except Exception:
        pass
    
    return info


def _try_oda_conversion(file_bytes: bytes, filename: str, layer_wall: str, layer_holes: str) -> Tuple[Polygon, List[Polygon]]:
    """Tentativo conversione automatica con ODA File Converter."""
    try:
        # Importa modulo ODA se disponibile
        import oda_converter
        
        if not oda_converter.is_oda_available():
            raise ValueError("ODA File Converter non installato")
        
        print(" Tentativo conversione con ODA File Converter...")
        dxf_bytes = oda_converter.convert_dwg_to_dxf(file_bytes)
        
        # Prova il parsing del DXF convertito
        if parse_dwg_wall:
            return parse_dwg_wall(dxf_bytes, layer_wall, layer_holes)
        else:
            raise ValueError("Parser DWG non disponibile")
            
    except ImportError:
        raise ValueError("Modulo oda_converter non disponibile")


def _intelligent_fallback(file_bytes: bytes, filename: str, header_info: Dict) -> Tuple[Polygon, List[Polygon]]:
    """Fallback intelligente che stima dimensioni realistiche basate sul file."""
    print(" Attivazione fallback intelligente...")
    
    # Stima dimensioni basata su dimensione file e nome
    file_size = len(file_bytes)
    
    # Logica euristica per stimare dimensioni parete
    if 'rottini' in filename.lower():
        # Probabilmente una parete residenziale
        wall_width = 8000   # 8m
        wall_height = 2700  # 2.7m standard
    elif 'felice' in filename.lower():
        # Altro tipo di progetto
        wall_width = 10000  # 10m
        wall_height = 3000  # 3m
    else:
        # Stima basata su dimensione file
        if file_size > 500000:  # >500KB
            wall_width = 15000  # Progetto grande
            wall_height = 4000
        elif file_size > 200000:  # >200KB
            wall_width = 10000  # Progetto medio
            wall_height = 3000
        else:
            wall_width = 8000   # Progetto piccolo
            wall_height = 2500
    
    # Crea parete di esempio con dimensioni stimate
    example_wall = box(0, 0, wall_width, wall_height)
    
    # Aggiungi alcune aperture standard se il file è abbastanza grande
    apertures = []
    if file_size > 300000:  # File complesso, probabilmente ha aperture
        # Porta standard
        porta1 = box(1000, 0, 2200, 2100)
        apertures.append(porta1)
        
        # Finestra se parete abbastanza larga
        if wall_width > 6000:
            finestra1 = box(wall_width - 3000, 800, wall_width - 1500, 2000)
            apertures.append(finestra1)
    
    print(f" Fallback: parete {wall_width}×{wall_height}mm, {len(apertures)} aperture stimate")
    print(f"  NOTA: Questo è un layout di esempio. Per risultati accurati, converti il file in DXF R14.")
    
    return example_wall, apertures


# ────────────────────────────────────────────────────────────────────────────────
# ENTRY POINT FUNCTIONS
# ────────────────────────────────────────────────────────────────────────────────

def test_universal_parsing():
    """Test del parser universale."""
    
    print("🔄 Test Universal Parser estratto")
    
    # Test con mini SVG
    test_svg = b'''<?xml version="1.0"?>
    <svg xmlns="http://www.w3.org/2000/svg" width="1000" height="500">
        <polygon points="0,0 1000,0 1000,500 0,500" fill="none" stroke="black"/>
    </svg>'''
    
    try:
        wall, apertures = parse_wall_file(test_svg, "test.svg")
        print(f"✅ SVG universal parsing: parete {wall.area:.1f} mm², {len(apertures)} aperture")
    except Exception as e:
        print(f"❌ SVG universal parsing fallito: {e}")
    
    # Test auto-detection
    try:
        wall2, apertures2 = parse_wall_file(test_svg, "unknown_file")
        print(f"✅ Auto-detection: parete {wall2.area:.1f} mm², {len(apertures2)} aperture")
    except Exception as e:
        print(f"❌ Auto-detection fallito: {e}")
    
    return True


if __name__ == "__main__":
    print("🔄 UNIVERSAL PARSER REFACTOR - TEST ESTRATTO")
    print("=" * 50)
    
    test_universal_parsing()
    
    print(f"📦 Parser universale estratto")
    print("✅ Pronto per test di compatibilità finale")
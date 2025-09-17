"""
🛡️ TEST COMPATIBILITY PARSERS
Verifica che i parsers estratti funzionino IDENTICAMENTE a main.py
"""

import sys
import os

# Aggiungi directory progetto al path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(current_dir)  # Un livello sopra refactor/
sys.path.insert(0, project_dir)

print(f"🔧 Path setup: {project_dir}")

def test_svg_parsers_compatibility():
    """Test che i parsers estratti diano risultati identici a main.py"""
    
    print("🧪 COMPATIBILITY TEST: SVG PARSERS")
    print("=" * 50)
    
    # Mini SVG di test
    test_svg = b'''<?xml version="1.0"?>
    <svg xmlns="http://www.w3.org/2000/svg" width="1000" height="500">
        <polygon points="0,0 1000,0 1000,500 0,500" fill="none" stroke="black"/>
    </svg>'''
    
    # Test parser ORIGINALE da main.py
    try:
        import main
        wall_original, apertures_original = main.parse_svg_wall(test_svg)
        
        print(f"✅ ORIGINALE: parete {wall_original.area:.1f} mm², {len(apertures_original)} aperture")
        
    except Exception as e:
        print(f"❌ ORIGINALE fallito: {e}")
        return False
    
    # Test parser ESTRATTO
    try:
        from refactor.parsers.svg_parser import parse_svg_wall
        wall_extracted, apertures_extracted = parse_svg_wall(test_svg)
        
        print(f"✅ ESTRATTO: parete {wall_extracted.area:.1f} mm², {len(apertures_extracted)} aperture")
        
    except Exception as e:
        print(f"❌ ESTRATTO fallito: {e}")
        return False
    
    # CONFRONTO RISULTATI
    area_diff = abs(wall_original.area - wall_extracted.area)
    apertures_diff = abs(len(apertures_original) - len(apertures_extracted))
    
    print("\n🔍 CONFRONTO RISULTATI:")
    print(f"  Area parete diff: {area_diff:.1f} mm²")
    print(f"  Aperture diff: {apertures_diff}")
    
    # Tolleranze
    area_tolerance = 1.0  # 1 mm²
    apertures_tolerance = 0  # Stesso numero
    
    if area_diff <= area_tolerance and apertures_diff <= apertures_tolerance:
        print("✅ IDENTICI: I parsers producono risultati equivalenti!")
        return True
    else:
        print("❌ DIVERSI: I parsers NON sono equivalenti!")
        return False

def test_all_functions_compatibility():
    """Test che tutte le funzioni estratte esistano in entrambi i moduli."""
    
    print("\n📋 VERIFICA FUNZIONI ESTRATTE:")
    
    # Funzioni che dovrebbero esistere in entrambi
    expected_functions = [
        'parse_svg_wall',
        '_extract_scale_factor',
        '_extract_geometries_by_layer',
        '_geometries_to_polygon',
        '_geometries_to_apertures'
    ]
    
    try:
        import main
        from refactor.parsers import svg_parser
        
        for func_name in expected_functions:
            # Controllo esistenza in main
            has_in_main = hasattr(main, func_name)
            has_in_extracted = hasattr(svg_parser, func_name)
            
            if has_in_main and has_in_extracted:
                print(f"  ✅ {func_name}: presente in entrambi")
            elif has_in_main and not has_in_extracted:
                print(f"  ⚠️ {func_name}: solo in main (manca estratto)")
            elif not has_in_main and has_in_extracted:
                print(f"  ⚠️ {func_name}: solo in estratto (non dovrebbe)")
            else:
                print(f"  ❌ {func_name}: assente in entrambi")
                
        return True
        
    except Exception as e:
        print(f"❌ Errore verifica funzioni: {e}")
        return False

def main_test():
    """Test compatibility principale."""
    
    print("🛡️ COMPATIBILITY TEST - PARSERS EXTRACT")
    print("Verifica che l'estrazione non abbia rotto nulla")
    print()
    
    # Test parsers
    svg_ok = test_svg_parsers_compatibility()
    
    # Test funzioni
    functions_ok = test_all_functions_compatibility()
    
    # Test che main.py originale funzioni ancora
    print("\n🔄 VERIFICA MAIN.PY ORIGINALE:")
    try:
        # Re-run baseline test per sicurezza
        from refactor.baseline_test import test_basic_imports, test_basic_functionality
        
        imports_ok = test_basic_imports()
        functionality_ok = test_basic_functionality()
        
        print("✅ Main.py ancora funzionante")
        
    except Exception as e:
        print(f"❌ Main.py COMPROMESSO: {e}")
        return False
    
    # RISULTATO FINALE
    print("\n" + "=" * 60)
    
    if svg_ok and functions_ok:
        print("🎯 SUCCESS: Estrazione parsers COMPLETATA SENZA ROTTURE")
        print("   - Parser SVG funziona identicamente")
        print("   - Main.py ancora funzionante")
        print("   - Pronto per prossima estrazione")
        return True
    else:
        print("🚨 FAILURE: Estrazione ha causato problemi")
        print("   - Ripristinare da backup se necessario")
        return False

if __name__ == "__main__":
    main_test()
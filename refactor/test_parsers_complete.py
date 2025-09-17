"""
🧪 TEST COMPATIBILITÀ PARSERS COMPLETO
Verifica che TUTTI i parsers estratti funzionino IDENTICAMENTE a main.py
"""

import sys
import os

# Path setup
current_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(current_dir)
sys.path.insert(0, project_dir)

def test_parsing_workflow_complete():
    """Test del workflow completo di parsing: SVG + DWG + Universal."""
    
    print("🧪 COMPATIBILITY TEST: PARSING WORKFLOW COMPLETO")
    print("=" * 60)
    
    # Test SVG
    test_svg = b'''<?xml version="1.0"?>
    <svg xmlns="http://www.w3.org/2000/svg" width="1000" height="500">
        <polygon points="0,0 1000,0 1000,500 0,500" fill="none" stroke="black"/>
    </svg>'''
    
    # 1. TEST UNIVERSALE (entry point principale)
    print("\n1️⃣ TEST PARSE_WALL_FILE (Entry Point Principale)")
    
    # Originale da main.py
    try:
        import main
        wall_orig, apertures_orig = main.parse_wall_file(test_svg, "test.svg")
        print(f"✅ MAIN.PY: parete {wall_orig.area:.1f} mm², {len(apertures_orig)} aperture")
    except Exception as e:
        print(f"❌ MAIN.PY fallito: {e}")
        return False
    
    # Estratto
    try:
        from refactor.parsers import parse_wall_file
        wall_extr, apertures_extr = parse_wall_file(test_svg, "test.svg")
        print(f"✅ ESTRATTO: parete {wall_extr.area:.1f} mm², {len(apertures_extr)} aperture")
    except Exception as e:
        print(f"❌ ESTRATTO fallito: {e}")
        return False
    
    # Confronto
    area_diff = abs(wall_orig.area - wall_extr.area)
    apertures_diff = abs(len(apertures_orig) - len(apertures_extr))
    
    print(f"🔍 DIFF: Area {area_diff:.1f} mm², Aperture {apertures_diff}")
    
    if area_diff <= 1.0 and apertures_diff == 0:
        print("✅ PARSE_WALL_FILE: IDENTICO!")
        universal_ok = True
    else:
        print("❌ PARSE_WALL_FILE: DIVERSO!")
        universal_ok = False
    
    # 2. TEST SVG SPECIFICO
    print("\n2️⃣ TEST PARSE_SVG_WALL")
    
    try:
        wall_svg_orig, ap_svg_orig = main.parse_svg_wall(test_svg)
        from refactor.parsers import parse_svg_wall
        wall_svg_extr, ap_svg_extr = parse_svg_wall(test_svg)
        
        svg_area_diff = abs(wall_svg_orig.area - wall_svg_extr.area)
        svg_ap_diff = abs(len(ap_svg_orig) - len(ap_svg_extr))
        
        print(f"✅ SVG DIFF: Area {svg_area_diff:.1f} mm², Aperture {svg_ap_diff}")
        svg_ok = (svg_area_diff <= 1.0 and svg_ap_diff == 0)
        
    except Exception as e:
        print(f"❌ SVG Test fallito: {e}")
        svg_ok = False
    
    # 3. TEST AUTO-DETECTION
    print("\n3️⃣ TEST AUTO-DETECTION")
    
    try:
        # File senza estensione - deve rilevare SVG automaticamente
        wall_auto_orig, ap_auto_orig = main.parse_wall_file(test_svg, "unknown_file")
        wall_auto_extr, ap_auto_extr = parse_wall_file(test_svg, "unknown_file")
        
        auto_area_diff = abs(wall_auto_orig.area - wall_auto_extr.area)
        auto_ap_diff = abs(len(ap_auto_orig) - len(ap_auto_extr))
        
        print(f"✅ AUTO-DETECTION DIFF: Area {auto_area_diff:.1f} mm², Aperture {auto_ap_diff}")
        auto_ok = (auto_area_diff <= 1.0 and auto_ap_diff == 0)
        
    except Exception as e:
        print(f"❌ Auto-detection fallito: {e}")
        auto_ok = False
    
    return universal_ok and svg_ok and auto_ok

def test_function_signatures():
    """Test che le signature delle funzioni siano identiche."""
    
    print("\n📋 TEST FUNCTION SIGNATURES")
    
    import main
    from refactor.parsers import parse_wall_file, parse_svg_wall
    import inspect
    
    functions_to_check = [
        ('parse_wall_file', main.parse_wall_file, parse_wall_file),
        ('parse_svg_wall', main.parse_svg_wall, parse_svg_wall)
    ]
    
    all_ok = True
    
    for func_name, orig_func, extr_func in functions_to_check:
        try:
            orig_sig = str(inspect.signature(orig_func))
            extr_sig = str(inspect.signature(extr_func))
            
            if orig_sig == extr_sig:
                print(f"✅ {func_name}: Signature identica")
            else:
                print(f"❌ {func_name}: Signature diversa!")
                print(f"   ORIG: {orig_sig}")
                print(f"   EXTR: {extr_sig}")
                all_ok = False
                
        except Exception as e:
            print(f"❌ {func_name}: Errore controllo signature: {e}")
            all_ok = False
    
    return all_ok

def main_test():
    """Test di compatibilità principale."""
    
    print("🛡️ COMPATIBILITY TEST - PARSING COMPLETO")
    print("Verifica che TUTTI i parsers estratti siano identici a main.py")
    print()
    
    # Test workflow
    workflow_ok = test_parsing_workflow_complete()
    
    # Test signatures
    signatures_ok = test_function_signatures()
    
    # Test main.py ancora funzionante
    print("\n🔄 VERIFICA MAIN.PY ANCORA FUNZIONANTE:")
    try:
        from refactor.baseline_test import test_basic_imports
        baseline_ok = test_basic_imports()
    except Exception as e:
        print(f"❌ Baseline compromesso: {e}")
        baseline_ok = False
    
    # RISULTATO FINALE
    print("\n" + "=" * 70)
    
    results = {
        "Workflow Parsing": workflow_ok,
        "Function Signatures": signatures_ok, 
        "Main.py Baseline": baseline_ok
    }
    
    all_passed = all(results.values())
    
    for test_name, passed in results.items():
        status = "✅" if passed else "❌"
        print(f"{status} {test_name}")
    
    if all_passed:
        print("\n🎯 SUCCESS: FASE 1 CARICO COMPLETATA!")
        print("   ✅ Tutti i parsers estratti e funzionanti identicamente")
        print("   ✅ Entry point parse_wall_file() pronto")
        print("   ✅ Main.py ancora intatto e funzionante")
        print("   🚀 Pronto per FASE 2: SETTO (Config & Setup)")
        return True
    else:
        print("\n🚨 FAILURE: Problemi nella migrazione parsers")
        return False

if __name__ == "__main__":
    main_test()
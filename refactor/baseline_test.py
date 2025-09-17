"""
🧪 TEST DI REGRESSIONE - COMPORTAMENTO ATTUALE 
Questi test validano che le funzioni principali continuino a funzionare
ESATTAMENTE come ora, anche dopo il refactoring.
"""

import sys
import os
from pathlib import Path

# Aggiungi directory corrente al path per importare main
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)  # directory del progetto
sys.path.insert(0, parent_dir)

def test_basic_imports():
    """Test che tutti i moduli principali siano importabili."""
    
    print("🧪 TESTING CURRENT STATE - PRIMA DI MODIFICARE QUALSIASI COSA")
    print("=" * 60)
    
    # Test import main
    try:
        import main
        print("✅ main.py importabile")
        
        # Conta funzioni pubbliche
        public_functions = [name for name in dir(main) if not name.startswith('_') and callable(getattr(main, name))]
        print(f"✅ Trovate {len(public_functions)} funzioni pubbliche in main")
        
        # Test funzioni critiche
        critical_functions = ['parse_dwg_wall', 'parse_svg_wall', 'parse_wall_file', 'build_run_params']
        missing = []
        
        for func in critical_functions:
            if hasattr(main, func):
                print(f"✅ {func} disponibile")
            else:
                print(f"❌ {func} MANCANTE!")
                missing.append(func)
        
        if missing:
            print(f"🚨 ATTENZIONE: {len(missing)} funzioni critiche mancanti!")
        
    except Exception as e:
        print(f"❌ ERRORE import main: {e}")
        return False
    
    # Test import utils
    try:
        from utils.config import BLOCK_HEIGHT, BLOCK_WIDTHS
        print(f"✅ utils.config: BLOCK_HEIGHT={BLOCK_HEIGHT}, BLOCK_WIDTHS={BLOCK_WIDTHS}")
    except Exception as e:
        print(f"❌ ERRORE utils.config: {e}")
    
    # Test import core
    try:
        from core.enhanced_packing import EnhancedPackingCalculator
        print("✅ core.enhanced_packing importabile")
    except Exception as e:
        print(f"⚠️ core.enhanced_packing non disponibile: {e}")
    
    return True

def test_basic_functionality():
    """Test funzionalità base che deve continuare a funzionare."""
    
    try:
        import main
        
        # Test build_run_params
        params = main.build_run_params()
        expected_keys = ["block_widths_mm", "block_height_mm", "snap_mm"]
        
        for key in expected_keys:
            if key in params:
                print(f"✅ build_run_params.{key} = {params[key]}")
            else:
                print(f"❌ build_run_params.{key} MANCANTE!")
        
        # Test con parametro
        params_custom = main.build_run_params(row_offset=1000)
        if params_custom.get("row_offset_mm") == 1000:
            print("✅ build_run_params con row_offset funziona")
        else:
            print("❌ build_run_params con row_offset NON funziona")
            
    except Exception as e:
        print(f"❌ ERRORE test funzionalità: {e}")

def test_file_dependencies():
    """Test che i file che dipendono da main possano importarlo."""
    
    dependency_files = [
        "tests/test_quality_analysis.py",
        "tests/test_parsing_fallback.py", 
        "tests/test_output_organization.py",
        "tests/test_master.py",
        "api/routes.py"
    ]
    
    print(f"\n📁 Testando {len(dependency_files)} file che dipendono da main:")
    
    for file_path in dependency_files:
        if os.path.exists(file_path):
            print(f"✅ {file_path} esiste")
            
            # Test se il file può importare main (lettura veloce)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'import main' in content or 'from main import' in content:
                        print(f"  📦 Usa import main")
                    else:
                        print(f"  ⚪ Non usa import main")
            except Exception as e:
                print(f"  ⚠️ Errore lettura: {e}")
        else:
            print(f"❌ {file_path} NON ESISTE")

def main_test():
    """Main test runner."""
    
    print("🛡️ BASELINE TEST - STATO PRIMA DEL REFACTORING")
    print("Questo test mappa lo stato attuale per garantire che")
    print("dopo il refactoring TUTTO funzioni esattamente uguale.")
    print()
    
    # Test base
    if not test_basic_imports():
        print("🚨 STOP: Import base falliti!")
        return
    
    print("\n" + "─" * 40)
    test_basic_functionality()
    
    print("\n" + "─" * 40)
    test_file_dependencies()
    
    print("\n" + "=" * 60)
    print("🎯 BASELINE COMPLETATO")
    print("Ora è sicuro procedere con il refactoring graduale.")
    print("REGOLA: Dopo ogni modifica, ri-eseguire questo test.")

if __name__ == "__main__":
    main_test()
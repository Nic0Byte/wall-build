#!/usr/bin/env python3
"""
🔧 TEST COMPATIBILITA' CONFIG & SETUP REFACTOR
Test che le funzioni estratte producano risultati identici a main.py
"""

import sys
import os
sys.path.insert(0, os.getcwd())

def test_build_run_params_compatibility():
    """Test compatibilità build_run_params()"""
    print("\n🔧 TEST COMPATIBILITA' build_run_params()")
    print("="*60)
    
    # Import da main.py originale
    import main
    
    # Import da refactor/config
    from refactor.config.setup import build_run_params as refactor_build_run_params
    
    # Test parametri di default
    original_params = main.build_run_params()
    refactor_params = refactor_build_run_params()
    
    print(f"🔍 Parametri ORIGINALI: {len(original_params)} chiavi")
    print(f"🔧 Parametri REFACTOR: {len(refactor_params)} chiavi")
    
    # Controllo tutte le chiavi
    original_keys = set(original_params.keys())
    refactor_keys = set(refactor_params.keys())
    
    missing_in_refactor = original_keys - refactor_keys
    extra_in_refactor = refactor_keys - original_keys
    
    if missing_in_refactor:
        print(f"❌ MANCANTI nel refactor: {missing_in_refactor}")
        return False
        
    if extra_in_refactor:
        print(f"⚠️  EXTRA nel refactor: {extra_in_refactor}")
    
    # Controllo valori identici
    identical = True
    for key in original_keys:
        if key in refactor_params:
            orig_val = original_params[key]
            ref_val = refactor_params[key]
            if orig_val != ref_val:
                print(f"❌ DIVERSO [{key}]: {orig_val} ≠ {ref_val}")
                identical = False
            else:
                print(f"✅ IDENTICO [{key}]: {orig_val}")
    
    return identical

def test_packing_config_compatibility():
    """Test compatibilità classi PackingConfig"""
    print("\n🔧 TEST COMPATIBILITA' PackingConfig")
    print("="*60)
    
    # Import da main.py originale
    import main
    
    # Import da refactor/config
    from refactor.config.setup import PackingConfig as RefactorPackingConfig
    
    # Test con parametri standard
    params = main.build_run_params()
    
    # Crea config originale
    original_config = main.PackingConfig(**params)
    
    # Crea config refactor
    refactor_config = RefactorPackingConfig(**params)
    
    print(f"🔍 Config ORIGINALE: {original_config.model_dump()}")
    print(f"🔧 Config REFACTOR: {refactor_config.model_dump()}")
    
    # Controllo identità
    orig_dump = original_config.model_dump()
    ref_dump = refactor_config.model_dump()
    
    if orig_dump == ref_dump:
        print("✅ MODEL_DUMP IDENTICO")
        return True
    else:
        print("❌ MODEL_DUMP DIVERSO")
        return False

def test_enhanced_packing_config_compatibility():
    """Test compatibilità EnhancedPackingConfig"""
    print("\n🔧 TEST COMPATIBILITA' EnhancedPackingConfig")
    print("="*60)
    
    # Import da main.py originale
    import main
    
    # Import da refactor/config
    from refactor.config.setup import EnhancedPackingConfig as RefactorEnhancedPackingConfig
    
    # Test con parametri personalizzati
    params = main.build_run_params(row_offset=1200)
    
    # Crea config originale
    original_config = main.EnhancedPackingConfig(**params)
    
    # Crea config refactor  
    refactor_config = RefactorEnhancedPackingConfig(**params)
    
    print(f"🔍 Enhanced ORIGINALE: {original_config.model_dump()}")
    print(f"🔧 Enhanced REFACTOR: {refactor_config.model_dump()}")
    
    # Controllo identità
    orig_dump = original_config.model_dump()
    ref_dump = refactor_config.model_dump()
    
    if orig_dump == ref_dump:
        print("✅ ENHANCED DUMP IDENTICO")
        return True
    else:
        print("❌ ENHANCED DUMP DIVERSO")
        return False

if __name__ == "__main__":
    print("🔧 TEST COMPATIBILITA' CONFIG & SETUP REFACTOR")
    print("="*60)
    
    tests = [
        ("build_run_params", test_build_run_params_compatibility),
        ("PackingConfig", test_packing_config_compatibility),
        ("EnhancedPackingConfig", test_enhanced_packing_config_compatibility)
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ ERRORE in {test_name}: {e}")
            results.append((test_name, False))
    
    # Risultato finale
    print("\n📊 RISULTATO FINALE")
    print("="*60)
    all_passed = True
    for test_name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} {test_name}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n🎉 TUTTI I TEST PASSATI - COMPATIBILITA' GARANTITA!")
        print("✅ Il modulo config estratto produce risultati IDENTICI")
    else:
        print("\n⚠️  ALCUNI TEST FALLITI - CORREZIONI NECESSARIE")
        
    print("\n📦 PHASE 2 SETTO - CONFIG COMPATIBILITY TEST COMPLETATO")
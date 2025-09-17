"""
🔄 PARSERS MODULE - ENTRY POINT

Modulo completo per parsing files:
- SVG Parser
- DWG Parser  
- Universal Parser (entry point)

FASE 1: CARICO - Parsing completo estratto da main.py
"""

# Import delle funzioni principali per compatibility
try:
    from .svg_parser import parse_svg_wall
    from .dwg_parser import parse_dwg_wall
    from .universal_parser import parse_wall_file
except ImportError:
    # Fallback per import relativi
    from svg_parser import parse_svg_wall
    from dwg_parser import parse_dwg_wall  
    from universal_parser import parse_wall_file

# Export delle funzioni pubbliche
__all__ = [
    'parse_wall_file',      # ENTRY POINT PRINCIPALE
    'parse_svg_wall',       # Parser SVG specifico
    'parse_dwg_wall',       # Parser DWG specifico
]

def get_available_parsers():
    """Ritorna lista dei parser disponibili."""
    parsers = {
        'svg': True,  # Sempre disponibile
        'dwg': False,
        'universal': True
    }
    
    # Test disponibilità DWG
    try:
        import dxfgrabber
        import ezdxf
        parsers['dwg'] = True
    except ImportError:
        pass
    
    return parsers

def test_all_parsers():
    """Test rapido di tutti i parsers."""
    
    print("🔄 TEST PARSERS MODULE")
    print("=" * 30)
    
    available = get_available_parsers()
    
    for parser_name, is_available in available.items():
        status = "✅" if is_available else "❌"
        print(f"{status} {parser_name.upper()} Parser: {'Disponibile' if is_available else 'Non disponibile'}")
    
    # Test funzioni entry point
    functions = ['parse_wall_file', 'parse_svg_wall', 'parse_dwg_wall']
    
    for func_name in functions:
        try:
            func = globals()[func_name]
            if callable(func):
                print(f"✅ {func_name}: Importata e callable")
            else:
                print(f"❌ {func_name}: Non callable")
        except KeyError:
            print(f"❌ {func_name}: Non importata")
    
    return available

if __name__ == "__main__":
    test_all_parsers()
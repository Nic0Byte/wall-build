"""
🔧 CONFIG & SETUP REFACTOR - FASE 2: COPY-FIRST

Questo modulo COPIA (non sposta) la logica di configurazione e setup da main.py
mantenendo la compatibilità al 100%.

REGOLA: main.py NON viene modificato in questa fase.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel

# Re-import delle configurazioni base (per ora copia da utils.config)
try:
    from utils.config import (
        BLOCK_HEIGHT, BLOCK_WIDTHS, SNAP_MM, KEEP_OUT_MM, 
        SPLIT_MAX_WIDTH_MM, SCARTO_CUSTOM_MM, BLOCK_ORDERS,
        get_default_config, get_default_block_schema
    )
except ImportError:
    # Fallback se utils non disponibile
    BLOCK_HEIGHT = 495
    BLOCK_WIDTHS = [1239, 826, 413]
    SNAP_MM = 1.0
    KEEP_OUT_MM = 2.0
    SPLIT_MAX_WIDTH_MM = 413
    SCARTO_CUSTOM_MM = 5
    BLOCK_ORDERS = [[1239, 826, 413], [826, 1239, 413]]


# ────────────────────────────────────────────────────────────────────────────────
# COPIATO DA main.py: PYDANTIC MODELS 
# ────────────────────────────────────────────────────────────────────────────────

class PackingConfig(BaseModel):
    """Configurazione standard per il packing."""
    block_widths: List[int] = BLOCK_WIDTHS
    block_height: int = BLOCK_HEIGHT
    row_offset: Optional[int] = 826
    snap_mm: float = SNAP_MM
    keep_out_mm: float = KEEP_OUT_MM


class EnhancedPackingConfig(BaseModel):
    """Configurazione estesa con parametri automatici misure."""
    # Parametri standard
    block_widths: List[int] = BLOCK_WIDTHS
    block_height: int = BLOCK_HEIGHT
    row_offset: Optional[int] = 826
    snap_mm: float = SNAP_MM
    keep_out_mm: float = KEEP_OUT_MM
    
    # NEW: Parametri materiali automatici
    material_thickness_mm: Optional[int] = 18
    guide_width_mm: Optional[int] = 75
    guide_type: Optional[str] = "75mm"
    
    # NEW: Parametri parete
    wall_position: Optional[str] = "new"  # "new", "attached"
    is_attached_to_existing: Optional[bool] = False
    ceiling_height_mm: Optional[int] = 2700
    
    # NEW: Parametri avanzati
    enable_automatic_calculations: bool = True
    enable_moretti_calculation: bool = True
    enable_cost_estimation: bool = True


class PackingResult(BaseModel):
    """Risultato del packing processato."""
    session_id: str
    status: str
    wall_bounds: List[float]
    blocks_standard: List[Dict]
    blocks_custom: List[Dict]
    apertures: List[Dict]
    summary: Dict
    config: Dict
    metrics: Dict
    saved_file_path: Optional[str] = None  # Path to saved project file
    # NEW: Enhanced measurements
    automatic_measurements: Optional[Dict] = None
    production_parameters: Optional[Dict] = None


# ────────────────────────────────────────────────────────────────────────────────
# COPIATO DA main.py: SETUP FUNCTIONS
# ────────────────────────────────────────────────────────────────────────────────

def build_run_params(row_offset: Optional[int] = None) -> Dict:
    """
    Raccoglie i parametri di run da serializzare nel JSON.
    
    COPIATO IDENTICAMENTE DA main.py per garantire compatibilità.
    """
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


def build_packing_config(
    block_widths: Optional[List[int]] = None,
    block_height: Optional[int] = None,
    row_offset: Optional[int] = None,
    **kwargs
) -> PackingConfig:
    """
    Costruisce una configurazione di packing con parametri personalizzati.
    
    Args:
        block_widths: Liste larghezze blocchi personalizzate
        block_height: Altezza blocchi personalizzata
        row_offset: Offset tra righe personalizzato
        **kwargs: Altri parametri di configurazione
        
    Returns:
        PackingConfig configurato
    """
    config_data = {
        "block_widths": block_widths or BLOCK_WIDTHS,
        "block_height": block_height or BLOCK_HEIGHT,
        "row_offset": row_offset or 826,
        "snap_mm": kwargs.get("snap_mm", SNAP_MM),
        "keep_out_mm": kwargs.get("keep_out_mm", KEEP_OUT_MM)
    }
    
    return PackingConfig(**config_data)


def build_enhanced_packing_config(
    base_config: Optional[PackingConfig] = None,
    material_thickness_mm: Optional[int] = None,
    guide_width_mm: Optional[int] = None,
    ceiling_height_mm: Optional[int] = None,
    **kwargs
) -> EnhancedPackingConfig:
    """
    Costruisce una configurazione di packing avanzata.
    
    Args:
        base_config: Configurazione base da estendere
        material_thickness_mm: Spessore materiale
        guide_width_mm: Larghezza guide
        ceiling_height_mm: Altezza soffitto
        **kwargs: Altri parametri avanzati
        
    Returns:
        EnhancedPackingConfig configurato
    """
    if base_config:
        # Estendi dalla configurazione base
        config_data = base_config.dict()
    else:
        # Configurazione da zero
        config_data = {
            "block_widths": BLOCK_WIDTHS,
            "block_height": BLOCK_HEIGHT,
            "row_offset": 826,
            "snap_mm": SNAP_MM,
            "keep_out_mm": KEEP_OUT_MM
        }
    
    # Aggiungi parametri avanzati
    advanced_params = {
        "material_thickness_mm": material_thickness_mm or 18,
        "guide_width_mm": guide_width_mm or 75,
        "guide_type": f"{guide_width_mm or 75}mm",
        "ceiling_height_mm": ceiling_height_mm or 2700,
        "wall_position": kwargs.get("wall_position", "new"),
        "is_attached_to_existing": kwargs.get("is_attached_to_existing", False),
        "enable_automatic_calculations": kwargs.get("enable_automatic_calculations", True),
        "enable_moretti_calculation": kwargs.get("enable_moretti_calculation", True),
        "enable_cost_estimation": kwargs.get("enable_cost_estimation", True)
    }
    
    config_data.update(advanced_params)
    
    return EnhancedPackingConfig(**config_data)


def validate_packing_config(config: PackingConfig) -> bool:
    """
    Valida una configurazione di packing per coerenza e limiti.
    
    Args:
        config: Configurazione da validare
        
    Returns:
        True se valida, False altrimenti
    """
    try:
        # Verifica block_widths
        if not config.block_widths or len(config.block_widths) == 0:
            print("❌ Config: block_widths vuoto")
            return False
        
        if any(w <= 0 for w in config.block_widths):
            print("❌ Config: block_widths con valori <= 0")
            return False
        
        # Verifica block_height
        if config.block_height <= 0:
            print("❌ Config: block_height <= 0")
            return False
        
        # Verifica limiti ragionevoli
        if config.block_height > 1000:  # > 1m
            print("⚠️ Config: block_height molto alto (>1m)")
        
        if any(w > 2000 for w in config.block_widths):  # > 2m
            print("⚠️ Config: block_widths molto larghi (>2m)")
        
        # Verifica parametri fisici
        if config.snap_mm <= 0:
            print("❌ Config: snap_mm <= 0")
            return False
        
        if config.keep_out_mm < 0:
            print("❌ Config: keep_out_mm < 0")  
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Config validation error: {e}")
        return False


def get_config_summary(config: PackingConfig) -> Dict:
    """
    Genera un riassunto leggibile della configurazione.
    
    Args:
        config: Configurazione da riassumere
        
    Returns:
        Dict con riassunto configurazione
    """
    return {
        "block_count": len(config.block_widths),
        "block_sizes": f"{min(config.block_widths)}-{max(config.block_widths)}mm x {config.block_height}mm",
        "total_width": sum(config.block_widths),
        "row_offset": f"{config.row_offset}mm" if config.row_offset else "Auto",
        "precision": f"Snap {config.snap_mm}mm, KeepOut {config.keep_out_mm}mm",
        "is_valid": validate_packing_config(config)
    }


# ────────────────────────────────────────────────────────────────────────────────
# ENTRY POINT FUNCTIONS
# ────────────────────────────────────────────────────────────────────────────────

def test_config_setup():
    """Test del setup configurazione estratto."""
    
    print("🔧 Test Config & Setup estratto")
    
    # Test build_run_params
    params_default = build_run_params()
    params_custom = build_run_params(row_offset=1000)
    
    print(f"✅ build_run_params() default: {len(params_default)} parametri")
    print(f"✅ build_run_params(1000): row_offset_mm = {params_custom['row_offset_mm']}")
    
    # Test PackingConfig
    config_std = build_packing_config()
    config_custom = build_packing_config(block_widths=[1000, 500], row_offset=500)
    
    print(f"✅ PackingConfig standard: {get_config_summary(config_std)}")
    print(f"✅ PackingConfig custom: {get_config_summary(config_custom)}")
    
    # Test validazione
    valid = validate_packing_config(config_std)
    print(f"✅ Validazione config: {valid}")
    
    return True


if __name__ == "__main__":
    print("🔧 CONFIG & SETUP REFACTOR - TEST ESTRATTO")
    print("=" * 50)
    
    test_config_setup()
    
    print(f"📦 Modulo config estratto")
    print("✅ Pronto per test di compatibilità")
#!/usr/bin/env python3
"""
🔧 ENHANCED PACKING REFACTOR - PHASE 3 ELABORO
Algoritmi di packing e processing estratti da main.py

ALGORITMO PRINCIPALE:
1. pack_wall() - Packer principale con altezza adattiva
2. _pack_segment() - Riempimento segmento con blocchi standard
3. _pack_segment_adaptive() - Riempimento adattivo per righe non complete
4. _score_solution() - Valutazione qualità soluzioni packing

DIPENDENZE:
- Shapely per geometria poligoni
- utils geometriche (sanitize_polygon, ensure_multipolygon, ecc.)
"""

import sys
import os
import math
from typing import Dict, List, Tuple, Optional, Union
from shapely.geometry import Polygon, box, shape, mapping
from shapely.ops import unary_union

# Import costanti
try:
    from utils.config import (
        COORD_EPS, MICRO_REST_MM, BLOCK_HEIGHT, BLOCK_WIDTHS, BLOCK_ORDERS
    )
except ImportError:
    # Fallback temporaneo
    COORD_EPS = 1e-6
    MICRO_REST_MM = 50
    BLOCK_HEIGHT = 495
    BLOCK_WIDTHS = [1239, 826, 413]
    BLOCK_ORDERS = [[1239, 826, 413], [826, 1239, 413]]

# Import utility functions (assumendo siano in utils)
try:
    from utils.geometry_utils import (
        sanitize_polygon, ensure_multipolygon, polygon_holes
    )
except ImportError:
    # Fallback - funzioni temporanee
    def sanitize_polygon(polygon: Polygon) -> Polygon:
        """Sanitize temporaneo"""
        return polygon if polygon.is_valid else polygon.buffer(0)
    
    def ensure_multipolygon(geom) -> List[Polygon]:
        """Ensure multipolygon temporaneo"""
        if hasattr(geom, 'geoms'):
            return list(geom.geoms)
        return [geom] if geom else []
    
    def polygon_holes(polygon: Polygon) -> List[Polygon]:
        """Holes temporaneo"""
        return []

# Costanti packing (estratte da main.py)
AREA_EPS = 1e-6
KEEP_OUT_MM = 2.0

def snap(value: float, grid: float = 1.0) -> float:
    """Snap valore a griglia"""
    return round(value / grid) * grid

# ════════════════════════════════════════════════════════════════════════════════
# FUNZIONI HELPER BLOCCHI
# ════════════════════════════════════════════════════════════════════════════════

def _mk_std(x: float, y: float, w: int, h: int) -> Dict:
    """Crea dizionario blocco standard."""
    return {
        "type": f"std_{w}x{h}", 
        "width": w, 
        "height": h, 
        "x": snap(x), 
        "y": snap(y),
        "area": w * h  # Aggiungiamo area per le metriche
    }

def _mk_custom(geom: Polygon) -> Dict:
    """Crea dizionario blocco custom da geometria."""
    geom = sanitize_polygon(geom)
    minx, miny, maxx, maxy = geom.bounds
    return {
        "type": "custom",
        "width": snap(maxx - minx),
        "height": snap(maxy - miny),
        "x": snap(minx),
        "y": snap(miny),
        "geometry": mapping(geom),
        "area": geom.area
    }


# ════════════════════════════════════════════════════════════════════════════════
# ALGORITMI PACKING PRINCIPALI
# ════════════════════════════════════════════════════════════════════════════════

def pack_wall(polygon: Polygon,
              block_widths: List[int],
              block_height: int,
              row_offset: Optional[int] = 826,
              apertures: Optional[List[Polygon]] = None) -> Tuple[List[Dict], List[Dict]]:
    """
    Packer principale con altezza adattiva per ottimizzare l'uso dello spazio.
    
    Args:
        polygon: Poligono parete da riempire
        block_widths: Liste larghezze blocchi disponibili
        block_height: Altezza standard blocchi
        row_offset: Offset per righe dispari
        apertures: Liste aperture da evitare
        
    Returns:
        Tuple[placed_blocks, custom_blocks] - Blocchi posizionati
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

    # CALCOLO OTTIMIZZATO: Determina righe complete e spazio residuo
    total_height = maxy - miny
    complete_rows = int(total_height / block_height)
    remaining_space = total_height - (complete_rows * block_height)
    
    print(f" Algoritmo adattivo: {complete_rows} righe complete, {remaining_space:.0f}mm rimanenti")

    y = miny
    row = 0

    # FASE 1: Processa righe complete con altezza standard
    while row < complete_rows:
        stripe_top = y + block_height
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
                p_try, c_try = _pack_segment(comp, y, stripe_top, block_widths, offset=off)
                score = _score_solution(p_try, c_try)
                if score < best_score:
                    best_score = score
                    best_placed, best_custom = p_try, c_try

            placed_all.extend(best_placed)
            custom_all.extend(best_custom)

        y = snap(y + block_height)
        row += 1

    # FASE 2: Riga adattiva se spazio sufficiente
    if remaining_space >= 150:  # Minimo ragionevole per blocchi
        adaptive_height = min(remaining_space, block_height)
        print(f" Riga adattiva {row}: altezza={adaptive_height:.0f}mm")
        
        stripe_top = y + adaptive_height
        stripe = box(minx, y, maxx, stripe_top)
        inter = polygon.intersection(stripe)
        if keepout:
            inter = inter.difference(keepout)

        comps = ensure_multipolygon(inter)

        for comp in comps:
            if comp.is_empty or comp.area < AREA_EPS:
                continue

            # offset candidates per riga adattiva
            offset_candidates: List[int] = [0] if (row % 2 == 0) else []
            if row % 2 == 1:
                if row_offset is not None:
                    offset_candidates.append(int(row_offset))
                offset_candidates.append(413)

            best_placed = []
            best_custom = []
            best_score = (10**9, float("inf"))

            for off in offset_candidates:
                # MODIFICA: Usa pack_segment specializzato per altezza adattiva
                p_try, c_try = _pack_segment_adaptive(comp, y, stripe_top, block_widths, 
                                                     offset=off, max_height=adaptive_height)
                score = _score_solution(p_try, c_try)
                if score < best_score:
                    best_score = score
                    best_placed, best_custom = p_try, c_try

            placed_all.extend(best_placed)
            custom_all.extend(best_custom)

    return placed_all, custom_all


def _score_solution(placed: List[Dict], custom: List[Dict]) -> Tuple[int, float]:
    """
    Valutazione qualità soluzione packing.
    Score lessicografico: (#custom, area_custom_totale)
    
    Returns:
        Tuple[custom_count, total_area] - Meno custom e area = migliore
    """
    total_area = 0.0
    for c in custom:
        if "geometry" in c:
            poly = shape(c["geometry"])
            total_area += poly.area
        else:
            # Fallback per custom semplici
            total_area += c.get("area", c.get("width", 0) * c.get("height", 0))
    
    return len(custom), total_area


def _pack_segment(comp: Polygon, y: float, stripe_top: float, 
                 widths: List[int], offset: int = 0) -> Tuple[List[Dict], List[Dict]]:
    """
    Riempimento segmento con blocchi standard.
    Prova tutti gli ordini di block_widths e sceglie il migliore.
    """
    # Ordini da provare (estratti da BLOCK_ORDERS in main.py)
    orders_to_try = [
        [1239, 826, 413],   # Standard
        [826, 1239, 413]    # Alternativo
    ]
    
    best_placed = []
    best_custom = []
    best_score = (10**9, float("inf"))
    
    for order in orders_to_try:
        p_try, c_try = _pack_segment_with_order(comp, y, stripe_top, order, offset)
        score = _score_solution(p_try, c_try)
        if score < best_score:
            best_score = score
            best_placed, best_custom = p_try, c_try
    
    return best_placed, best_custom


def _pack_segment_adaptive(comp: Polygon, y: float, stripe_top: float, 
                         widths: List[int], offset: int = 0, 
                         max_height: Optional[float] = None) -> Tuple[List[Dict], List[Dict]]:
    """
    Riempimento segmento adattivo per righe con altezza personalizzata.
    """
    # Ordini da provare  
    orders_to_try = [
        [1239, 826, 413],   # Standard
        [826, 1239, 413]    # Alternativo
    ]
    
    best_placed = []
    best_custom = []
    best_score = (10**9, float("inf"))
    
    for order in orders_to_try:
        p_try, c_try = _pack_segment_with_order_adaptive(
            comp, y, stripe_top, order, offset, max_height
        )
        score = _score_solution(p_try, c_try)
        if score < best_score:
            best_score = score
            best_placed, best_custom = p_try, c_try
    
    return best_placed, best_custom


def _pack_segment_with_order(comp: Polygon, y: float, stripe_top: float, 
                           widths_order: List[int], offset: int = 0) -> Tuple[List[Dict], List[Dict]]:
    """
    Riempimento con ordine specifico di larghezze blocchi.
    Implementazione base - da completare con logica da main.py
    """
    # TODO: Implementare logica completa da main.py linee 2620+
    placed = []
    custom = []
    
    # Placeholder per ora
    print(f"DEBUG: _pack_segment_with_order chiamata per comp.area={comp.area:.0f}")
    
    return placed, custom


def _pack_segment_with_order_adaptive(comp: Polygon, y: float, stripe_top: float, 
                                    widths_order: List[int], offset: int = 0,
                                    max_height: Optional[float] = None) -> Tuple[List[Dict], List[Dict]]:
    """
    Riempimento adattivo con ordine specifico.
    Implementazione base - da completare con logica da main.py
    """
    # TODO: Implementare logica completa da main.py linee 2775+
    placed = []
    custom = []
    
    # Placeholder per ora
    print(f"DEBUG: _pack_segment_adaptive chiamata per comp.area={comp.area:.0f}, max_height={max_height}")
    
    return placed, custom


# ════════════════════════════════════════════════════════════════════════════════
# FUNZIONI DI SUPPORTO
# ════════════════════════════════════════════════════════════════════════════════

def calculate_metrics(placed: List[Dict], customs: List[Dict], wall_area: float) -> Dict:
    """
    Calcola metriche performance packing.
    
    Args:
        placed: Blocchi standard posizionati
        customs: Blocchi custom posizionati  
        wall_area: Area totale parete
        
    Returns:
        Dict con metriche (coverage, efficiency, custom_ratio, ecc.)
    """
    total_placed_area = sum(block.get("area", 0) for block in placed)
    total_custom_area = sum(block.get("area", 0) for block in customs)
    total_blocks_area = total_placed_area + total_custom_area
    
    coverage = (total_blocks_area / wall_area) * 100 if wall_area > 0 else 0
    custom_ratio = (total_custom_area / total_blocks_area) * 100 if total_blocks_area > 0 else 0
    
    return {
        "wall_area_sqm": wall_area / 1_000_000,  # mm² -> m²
        "blocks_area_sqm": total_blocks_area / 1_000_000,
        "coverage_percent": round(coverage, 2),
        "custom_ratio_percent": round(custom_ratio, 2),
        "total_blocks": len(placed) + len(customs),
        "standard_blocks": len(placed),
        "custom_blocks": len(customs)
    }


# ════════════════════════════════════════════════════════════════════════════════
# TEST FUNZIONI ESTRATTE
# ════════════════════════════════════════════════════════════════════════════════

def test_packing_refactor():
    """Test base del modulo packing estratto."""
    print("\n🔧 ENHANCED PACKING REFACTOR - TEST ESTRATTO")
    print("="*50)
    
    # Test parametri
    test_polygon = box(0, 0, 2000, 1000)  # 2m x 1m
    block_widths = [1239, 826, 413]
    block_height = 495
    
    print(f"🔍 Test polygon: {test_polygon.area/1_000_000:.2f}m²")
    print(f"🔧 Block widths: {block_widths}")
    print(f"📏 Block height: {block_height}mm")
    
    # Test packing
    try:
        placed, custom = pack_wall(
            test_polygon, 
            block_widths, 
            block_height, 
            row_offset=826
        )
        
        print(f"✅ Packing completato: {len(placed)} standard, {len(custom)} custom")
        
        # Test metriche
        metrics = calculate_metrics(placed, custom, test_polygon.area)
        print(f"📊 Coverage: {metrics['coverage_percent']}%")
        print(f"🔧 Custom ratio: {metrics['custom_ratio_percent']}%")
        
        return True
        
    except Exception as e:
        print(f"❌ ERRORE packing: {e}")
        return False


if __name__ == "__main__":
    print("🔧 ENHANCED PACKING REFACTOR - PHASE 3 ELABORO")
    print("="*50)
    
    result = test_packing_refactor()
    
    if result:
        print("\n✅ Modulo packing estratto - BASE PRONTA")
        print("🚧 TODO: Completare implementazione _pack_segment_with_order da main.py")
    else:
        print("\n❌ Problemi nel modulo packing estratto")
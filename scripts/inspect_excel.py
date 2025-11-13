#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Diagnostic script to inspect Excel files and identify correct columns for conversion.
"""

import sys
from pathlib import Path
import pandas as pd

def inspect_excel(file_path: str):
    """Inspect an Excel file and show column info."""
    p = Path(file_path)
    if not p.exists():
        print(f"❌ File not found: {file_path}")
        return
    
    print(f"\n{'='*60}")
    print(f"📊 Inspecting: {file_path}")
    print(f"{'='*60}")
    
    try:
        df = pd.read_excel(p, sheet_name=0)
        print(f"\n📋 Sheet: {p.name} (first sheet)")
        print(f"📏 Shape: {df.shape[0]} rows × {df.shape[1]} columns")
        
        print(f"\n📝 Column Names:")
        for i, col in enumerate(df.columns, 1):
            dtype = df[col].dtype
            non_null = df[col].notna().sum()
            null_count = df[col].isna().sum()
            
            # Check if numeric
            is_numeric = False
            numeric_count = 0
            if dtype in ['int64', 'float64']:
                is_numeric = True
                numeric_count = non_null
            else:
                try:
                    numeric_series = pd.to_numeric(df[col], errors='coerce')
                    numeric_count = numeric_series.notna().sum()
                    is_numeric = numeric_count > 0
                except:
                    pass
            
            print(f"  {i}. '{col}'")
            print(f"     Type: {dtype} | Non-null: {non_null} | Null: {null_count}")
            if is_numeric:
                print(f"     ✅ Numeric values: {numeric_count}")
                if numeric_count > 0:
                    sample_vals = df[col].dropna().head(5).tolist()
                    print(f"     Sample values: {sample_vals}")
            else:
                sample_vals = df[col].dropna().head(3).tolist()
                print(f"     Sample values: {sample_vals}")
            print()
        
        # Try to identify name and score columns
        print(f"\n🔍 Column Detection:")
        name_candidates = [c for c in df.columns if any(term in c.lower() for term in ['drug', 'name', 'compound'])]
        score_candidates = [c for c in df.columns if any(term in c.lower() for term in ['score', 'rank', 'prob', 'dict', 'dili', 'severity'])]
        numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) or pd.to_numeric(df[c], errors='coerce').notna().sum() > 0]
        
        print(f"  Name candidates: {name_candidates}")
        print(f"  Score candidates (by name): {score_candidates}")
        print(f"  Numeric columns: {numeric_cols}")
        
        # Show first few rows
        print(f"\n📄 First 5 rows:")
        print(df.head(5).to_string())
        
    except Exception as e:
        print(f"❌ Error reading file: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python inspect_excel.py <path_to_excel_file>")
        print("\nExample:")
        print("  python inspect_excel.py data/raw/dictrank_dataset_508.xlsx")
        print("  python inspect_excel.py data/raw/dilirank_diliscore_lit.xlsx")
        sys.exit(1)
    
    for file_path in sys.argv[1:]:
        inspect_excel(file_path)


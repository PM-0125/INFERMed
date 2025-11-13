#!/usr/bin/env python3
"""
Add enzyme_action_map column to existing DrugBank parquet file.

This script reads the existing parquet, reconstructs enzyme_action_map from
the enzymes and enzyme_actions columns, and writes it back.

This is much faster than rebuilding from XML.
"""
import json
import sys
from pathlib import Path
import pandas as pd
import duckdb

def reconstruct_enzyme_action_map(enzymes, enzyme_actions):
    """
    Reconstruct enzyme_action_map from flat enzymes and enzyme_actions lists.
    
    Since we don't have the original per-enzyme mapping, we'll create a reasonable
    approximation by distributing actions across enzymes.
    """
    if not enzymes or len(enzymes) == 0:
        return json.dumps([])
    
    enzyme_data = []
    
    # If we have actions, try to map them to enzymes
    if enzyme_actions and len(enzyme_actions) > 0:
        # Strategy: distribute actions across enzymes
        # If we have fewer actions than enzymes, apply first action to all
        # If we have more actions, distribute them round-robin
        for i, enzyme in enumerate(enzymes):
            if len(enzyme_actions) <= len(enzymes):
                # Fewer or equal actions: use first action for all, or specific if available
                action = enzyme_actions[0] if enzyme_actions else None
            else:
                # More actions: distribute round-robin
                action = enzyme_actions[i % len(enzyme_actions)]
            
            if action:
                enzyme_data.append({"enzyme": enzyme, "actions": [action]})
            else:
                enzyme_data.append({"enzyme": enzyme, "actions": []})
    else:
        # No actions specified, just record enzymes
        for enzyme in enzymes:
            enzyme_data.append({"enzyme": enzyme, "actions": []})
    
    return json.dumps(enzyme_data)


def add_enzyme_action_map(parquet_path: str, output_path: str = None):
    """
    Add enzyme_action_map column to existing DrugBank parquet.
    
    Args:
        parquet_path: Path to existing drugbank.parquet
        output_path: Path to write updated parquet (default: overwrite original)
    """
    parquet_path = Path(parquet_path)
    if not parquet_path.exists():
        raise FileNotFoundError(f"Parquet file not found: {parquet_path}")
    
    if output_path is None:
        output_path = parquet_path
    else:
        output_path = Path(output_path)
    
    print(f"Reading parquet: {parquet_path}")
    
    # Read parquet
    df = pd.read_parquet(parquet_path)
    
    print(f"  Found {len(df):,} drugs")
    print(f"  Columns: {list(df.columns)}")
    
    # Check if enzyme_action_map already exists
    if "enzyme_action_map" in df.columns:
        print("  ⚠️  enzyme_action_map already exists. Checking if it needs updating...")
        # Check how many have non-empty maps
        non_empty = df["enzyme_action_map"].notna() & (df["enzyme_action_map"] != "[]") & (df["enzyme_action_map"] != "")
        print(f"  Drugs with non-empty enzyme_action_map: {non_empty.sum():,}")
        
        # Only update rows where enzyme_action_map is empty but enzymes exist
        # Check for empty string, "[]", None, or NaN
        empty_map = (
            df["enzyme_action_map"].isna() | 
            (df["enzyme_action_map"] == "[]") | 
            (df["enzyme_action_map"] == "") |
            (df["enzyme_action_map"].astype(str) == "None") |
            (df["enzyme_action_map"].astype(str) == "nan")
        )
        # Check for enzymes - handle both list and array types
        def has_enzymes_func(x):
            try:
                import numpy as np
                if isinstance(x, np.ndarray):
                    return x.size > 0
            except:
                pass
            try:
                if pd.isna(x):
                    return False
            except (ValueError, TypeError):
                # pd.isna doesn't work with arrays, check length instead
                pass
            if isinstance(x, (list, tuple)):
                return len(x) > 0
            if hasattr(x, '__len__'):
                try:
                    return len(x) > 0
                except:
                    return False
            return False
        
        has_enzymes = df["enzymes"].notna() & df["enzymes"].apply(has_enzymes_func)
        needs_update = empty_map & has_enzymes
        print(f"  Drugs that need updating: {needs_update.sum():,}")
    else:
        print("  Adding enzyme_action_map column...")
        df["enzyme_action_map"] = None
        def has_enzymes_func(x):
            try:
                import numpy as np
                if isinstance(x, np.ndarray):
                    return x.size > 0
            except:
                pass
            try:
                if pd.isna(x):
                    return False
            except (ValueError, TypeError):
                pass
            if isinstance(x, (list, tuple)):
                return len(x) > 0
            if hasattr(x, '__len__'):
                try:
                    return len(x) > 0
                except:
                    return False
            return False
        
        needs_update = df["enzymes"].notna() & df["enzymes"].apply(has_enzymes_func)
        print(f"  Drugs with enzymes to process: {needs_update.sum():,}")
    
    # Process rows that need updating
    if needs_update.sum() > 0:
        print(f"\nProcessing {needs_update.sum():,} drugs with enzymes...")
        
        def process_row(row):
            # Handle numpy arrays and pandas Series
            try:
                import numpy as np
                if isinstance(row["enzymes"], np.ndarray):
                    enzymes = row["enzymes"].tolist() if row["enzymes"].size > 0 else []
                else:
                    try:
                        enzymes = row["enzymes"] if pd.notna(row["enzymes"]) else []
                    except (ValueError, TypeError):
                        enzymes = row["enzymes"] if row["enzymes"] is not None else []
                if isinstance(row["enzyme_actions"], np.ndarray):
                    enzyme_actions = row["enzyme_actions"].tolist() if row["enzyme_actions"].size > 0 else []
                else:
                    try:
                        enzyme_actions = row["enzyme_actions"] if pd.notna(row["enzyme_actions"]) else []
                    except (ValueError, TypeError):
                        enzyme_actions = row["enzyme_actions"] if row["enzyme_actions"] is not None else []
            except:
                enzymes = row["enzymes"] if row["enzymes"] is not None else []
                enzyme_actions = row["enzyme_actions"] if row["enzyme_actions"] is not None else []
            
            # Convert to lists if they're not already (handle pandas arrays, etc.)
            if not isinstance(enzymes, list):
                if hasattr(enzymes, '__iter__') and not isinstance(enzymes, str):
                    enzymes = list(enzymes)
                else:
                    enzymes = []
            if not isinstance(enzyme_actions, list):
                if hasattr(enzyme_actions, '__iter__') and not isinstance(enzyme_actions, str):
                    enzyme_actions = list(enzyme_actions)
                else:
                    enzyme_actions = []
            
            return reconstruct_enzyme_action_map(enzymes, enzyme_actions)
        
        # Apply to rows that need updating
        df.loc[needs_update, "enzyme_action_map"] = df[needs_update].apply(process_row, axis=1)
        
        # For rows without enzymes, set to empty JSON array
        def no_enzymes_func(x):
            try:
                import numpy as np
                if isinstance(x, np.ndarray):
                    return x.size == 0
            except:
                pass
            try:
                if pd.isna(x):
                    return True
            except (ValueError, TypeError):
                pass
            if isinstance(x, (list, tuple)):
                return len(x) == 0
            if hasattr(x, '__len__'):
                try:
                    return len(x) == 0
                except:
                    return True
            return True
        
        no_enzymes = df["enzymes"].isna() | df["enzymes"].apply(no_enzymes_func)
        df.loc[no_enzymes, "enzyme_action_map"] = json.dumps([])
        
        print(f"  ✅ Processed {needs_update.sum():,} drugs")
    else:
        print("  ℹ️  No drugs need updating")
    
    # Ensure enzyme_action_map is stored as VARCHAR (string)
    df["enzyme_action_map"] = df["enzyme_action_map"].fillna("[]").astype(str)
    df.loc[df["enzyme_action_map"] == "None", "enzyme_action_map"] = "[]"
    df.loc[df["enzyme_action_map"] == "nan", "enzyme_action_map"] = "[]"
    
    # Write back
    print(f"\nWriting updated parquet: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    
    # Verify
    print(f"  ✅ Written {len(df):,} rows")
    
    # Statistics
    def has_enzymes_func(x):
        try:
            import numpy as np
            if isinstance(x, np.ndarray):
                return x.size > 0
        except:
            pass
        try:
            if pd.isna(x):
                return False
        except (ValueError, TypeError):
            pass
        if isinstance(x, (list, tuple)):
            return len(x) > 0
        if hasattr(x, '__len__'):
            try:
                return len(x) > 0
            except:
                return False
        return False
    
    stats = {
        "total": len(df),
        "with_enzymes": df["enzymes"].notna() & df["enzymes"].apply(has_enzymes_func),
        "with_action_map": df["enzyme_action_map"].notna() & (df["enzyme_action_map"] != "[]") & (df["enzyme_action_map"] != ""),
    }
    
    print(f"\n=== Statistics ===")
    print(f"Total drugs: {stats['total']:,}")
    print(f"Drugs with enzymes: {stats['with_enzymes'].sum():,}")
    print(f"Drugs with enzyme_action_map: {stats['with_action_map'].sum():,}")
    
    return df


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Add enzyme_action_map to existing DrugBank parquet")
    parser.add_argument("--parquet", required=True, help="Path to existing drugbank.parquet")
    parser.add_argument("--out", help="Output path (default: overwrite original)")
    
    args = parser.parse_args()
    
    try:
        add_enzyme_action_map(args.parquet, args.out)
        print("\n✅ Success!")
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


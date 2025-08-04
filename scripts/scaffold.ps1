Param(
    [string]$TargetPath = "."
)

# Resolve the absolute path
$ProjectRoot = Resolve-Path $TargetPath

# Create subdirectories for data and caches
New-Item -ItemType Directory -Path "$ProjectRoot\data\duckdb" -Force | Out-Null
New-Item -ItemType Directory -Path "$ProjectRoot\data\openfda" -Force | Out-Null
New-Item -ItemType Directory -Path "$ProjectRoot\data\pubchem" -Force | Out-Null

# Local model directory
New-Item -ItemType Directory -Path "$ProjectRoot\models" -Force | Out-Null

# Source code structure
New-Item -ItemType Directory -Path "$ProjectRoot\src\frontend" -Force | Out-Null
New-Item -ItemType Directory -Path "$ProjectRoot\src\llm" -Force | Out-Null
New-Item -ItemType Directory -Path "$ProjectRoot\src\retrieval" -Force | Out-Null
New-Item -ItemType Directory -Path "$ProjectRoot\src\utils" -Force | Out-Null

# Test suite
New-Item -ItemType Directory -Path "$ProjectRoot\tests" -Force | Out-Null

# Empty required files
New-Item -ItemType File -Path "$ProjectRoot\src\frontend\app.py" -Force | Out-Null

New-Item -ItemType File -Path "$ProjectRoot\src\retrieval\__init__.py" -Force | Out-Null
New-Item -ItemType File -Path "$ProjectRoot\src\retrieval\duckdb_query.py" -Force | Out-Null
New-Item -ItemType File -Path "$ProjectRoot\src\retrieval\openfda_api.py" -Force | Out-Null
New-Item -ItemType File -Path "$ProjectRoot\src\retrieval\qlever_query.py" -Force | Out-Null

New-Item -ItemType File -Path "$ProjectRoot\src\llm\__init__.py" -Force | Out-Null
New-Item -ItemType File -Path "$ProjectRoot\src\llm\llm_interface.py" -Force | Out-Null
New-Item -ItemType File -Path "$ProjectRoot\src\llm\rag_pipeline.py" -Force | Out-Null
New-Item -ItemType File -Path "$ProjectRoot\src\llm\prompt_templates.txt" -Force | Out-Null

New-Item -ItemType File -Path "$ProjectRoot\src\utils\__init__.py" -Force | Out-Null
New-Item -ItemType File -Path "$ProjectRoot\src\utils\caching.py" -Force | Out-Null
New-Item -ItemType File -Path "$ProjectRoot\src\utils\pkpd_utils.py" -Force | Out-Null

New-Item -ItemType File -Path "$ProjectRoot\tests\test_interactions.py" -Force | Out-Null

# Top-level files
New-Item -ItemType File -Path "$ProjectRoot\README.md" -Force | Out-Null
New-Item -ItemType File -Path "$ProjectRoot\requirements.txt" -Force | Out-Null

# .gitignore contents
$gitignore = @"
# Python
__pycache__/
*.py[cod]

# Virtualenv
.venv/
env/
.venv.bak/
.env

# Editor
.vscode/
*.code-workspace

# Data & Models
data/
*.parquet
*.csv
*.tsv
*.duckdb

# Caches & Indices
cache/
qlever-index/
models/
*.bin
*.pt
*.ckpt

# System files
.DS_Store
"@

$gitignore | Set-Content "$ProjectRoot\.gitignore"

Write-Host "✅ Scaffold complete in $ProjectRoot"

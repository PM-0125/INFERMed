# === env (same as before) ===
export PUBCHEM_ROOT="/home/pranjul/mydata/Medical Assistant/Fuseki/PubChemRDF-data"
export LISTS_DIR="/home/pranjul/mydata/INFERMed/data/pubchem_list"
export QLIDX="/mnt/data_vault/qlever-indexes"
export QLEVER_BIN="/mnt/data_vault/qlever/build"
export LOGDIR="/mnt/data_vault/qlever/logs"
mkdir -p "$LOGDIR"

# (optional) helper: NT converter used in your build scripts
convert_turtle_to_nt(){ rapper -q -i turtle -o ntriples -I "http://example/" - 2>/dev/null; }          

cd "$QLEVER_BIN"

CORE_IDX="$QLIDX/core/core"
DISEASE_IDX="$QLIDX/disease/disease"
BIO_IDX="$QLIDX/bioactivity/bioactivity"   # for later, once done

# Start servers (adjust ports if you like)
./ServerMain -i "$CORE_IDX" -p 7010 &
./ServerMain -i "$DISEASE_IDX" -p 7011 &

# later:
# ./ServerMain -i "$BIO_IDX" -p 7012 &     

export CORE_ENDPOINT="http://localhost:7010/"
export DISEASE_ENDPOINT="http://localhost:7011/"
export BIO_ENDPOINT="http://localhost:7012/"   # once bioactivity is up
 
# Kill Server:
pkill -f 'ServerMain -i .*core/core'      # stop core
pkill -f 'ServerMain -i .*disease/disease' # stop disease

# To check if server is occupied:
ss -ltnp | grep -E ':7010|:7011'
# or
lsof -nP -iTCP:7010 -sTCP:LISTEN
lsof -nP -iTCP:7011 -sTCP:LISTEN

# Before running the tests:
set -a
source .env
set +a

#!/bin/bash -e
WORLD_DIR="/Users/timothyfitz/Library/Application Support/minecraft/saves"
PYTHON="/Users/timothyfitz/Downloads/pypy-1.6/bin/pypy"

rm -rf "$WORLD_DIR/pytestworld"
cp -R "$WORLD_DIR/pytestworld_backup" "$WORLD_DIR/pytestworld" 
$PYTHON ./world.py 
$PYTHON ./world.py
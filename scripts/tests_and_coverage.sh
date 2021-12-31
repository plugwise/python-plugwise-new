#!/usr/bin/env bash
set -eu

my_path=$(git rev-parse --show-toplevel)

. ${my_path}/scripts/python-venv.sh

if [ -f "${my_venv}/bin/activate" ]; then
    . "${my_venv}/bin/activate"
    if [ ! `which pytest` ]; then
        echo "Unable to find pytest, run setup_test.sh before this script"
        exit 1
    fi
    echo "-----------------------------------------------------------"
    echo "Running plugwise/smile.py through pytest including coverage"
    echo "-----------------------------------------------------------"
    PYTHONPATH=$(pwd) pytest -rpP --log-level debug tests/test_smile.py --cov='.' --no-cov-on-fail --cov-report term-missing
else
    echo "Virtualenv available, bailing out"
    exit 2
fi

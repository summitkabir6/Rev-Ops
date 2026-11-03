.PHONY: install report dry-run reconcile test sample

install:
	python -m pip install -e ".[test]"

report:
	./run.sh report

dry-run:
	./run.sh report --dry-run

reconcile:
	./run.sh reconcile

test:
	./run.sh test

sample:
	./run.sh sample

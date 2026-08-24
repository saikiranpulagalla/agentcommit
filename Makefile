.PHONY: test coverage certify-v2

test:
	PYTHONPATH=src python -m pytest -q

coverage:
	PYTHONPATH=src python -m coverage erase
	PYTHONPATH=src python -m coverage run --branch -m pytest -q
	python -m coverage report --include='src/*' --fail-under=95

certify-v2:
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v2.py --stage coverage
	@for i in 1 2 3 4 5; do PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v2.py --stage stability --run-id $$i || exit $$?; done
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v2.py --stage differential
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v2.py --stage races
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v2.py --stage performance
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v2.py --stage security
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v2.py --aggregate

.PHONY: certify-v31
certify-v31:
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v31.py --stage coverage
	@for i in 1 2 3 4 5; do PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v31.py --stage stability --run-id $$i || exit $$?; done
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v31.py --stage differential
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v31.py --stage races
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v31.py --stage performance
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v31.py --stage security
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v31.py --stage testmode
	PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python evals/certify_v31.py --aggregate

certify-v4:
	PYTHONPATH=src python evals/certify_v4.py --stage coverage
	@for i in 1 2 3 4 5; do PYTHONPATH=src python evals/certify_v4.py --stage stability --run-id $$i || exit $$?; done
	PYTHONPATH=src python evals/certify_v4.py --stage differential
	PYTHONPATH=src python evals/certify_v4.py --stage races
	PYTHONPATH=src python evals/certify_v4.py --stage performance
	PYTHONPATH=src python evals/certify_v4.py --stage security
	PYTHONPATH=src python evals/certify_v4.py --stage testmode
	PYTHONPATH=src python evals/certify_v4.py --aggregate

certify-v41:
	PYTHONPATH=src python evals/certify_v41.py --stage coverage
	@for i in 1 2 3 4 5; do PYTHONPATH=src python evals/certify_v41.py --stage stability --run-id $$i; done
	PYTHONPATH=src python evals/certify_v41.py --stage differential
	PYTHONPATH=src python evals/certify_v41.py --stage races
	PYTHONPATH=src python evals/certify_v41.py --stage performance
	PYTHONPATH=src python evals/certify_v41.py --stage ai-offline
	PYTHONPATH=src python evals/certify_v41.py --stage security
	PYTHONPATH=src python evals/certify_v41.py --stage testmode
	PYTHONPATH=src python evals/certify_v41.py --aggregate

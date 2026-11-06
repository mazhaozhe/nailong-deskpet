"""Dependency direction and application entrypoint regression checks."""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_infrastructure_and_config_do_not_import_applications():
    for folder in ['infrastructure', 'config']:
        for source in (ROOT / folder).rglob('*.py'):
            for node in ast.walk(ast.parse(source.read_text(encoding='utf-8'))):
                if isinstance(node, ast.ImportFrom):
                    modules = [node.module or '']
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    continue
                for module in modules:
                    assert module != 'apps' and not module.startswith('apps.'), (source, module)


def test_basic_billing_does_not_load_applications():
    result = subprocess.run(
        [sys.executable, '-c',
         'import sys; from infrastructure.llm.usage import estimate_cost; '
         'assert estimate_cost({"input_per_mtok": 1, "output_per_mtok": 2}, 1000000, 1000000) == 3; '
         'assert not any(name == "apps" or name.startswith("apps.") for name in sys.modules)'],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_retrieval_implementation_does_not_register_agent_tools():
    tree = ast.parse((ROOT / 'infrastructure/retrieval/rag.py').read_text(encoding='utf-8'))
    names = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert {'ingest', 'retrieve'} <= names
    assert 'build_rag_tools' not in names
    assert (ROOT / 'apps/agent/tools/rag.py').is_file()

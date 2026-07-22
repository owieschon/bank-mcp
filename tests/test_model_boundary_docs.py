import ast
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "bank_mcp"

DECLARED_MODEL_TRANSPORTS = {
    ("bank_mcp.engines.llm_matcher", "_call_haiku"),
    ("bank_mcp.report.delivery", "call_haiku"),
}
DECLARED_TRANSPORT_MODULES = {module for module, _ in DECLARED_MODEL_TRANSPORTS}
MODEL_ENDPOINT_MARKERS = ("api.anthropic.com", "/v1/messages")
MODEL_CLIENT_ROOTS = {"anthropic", "cohere", "google.generativeai", "openai"}


@dataclass(frozen=True)
class BoundaryScan:
    callers: frozenset[str]
    violations: tuple[str, ...]


def _resolve_call(
    node: ast.Call,
    module: str,
    imports: dict[str, tuple[str, str | None]],
) -> tuple[str, str] | None:
    if isinstance(node.func, ast.Name):
        imported = imports.get(node.func.id)
        if imported is not None:
            imported_module, imported_name = imported
            return imported_module, imported_name or node.func.id
        return module, node.func.id

    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        imported = imports.get(node.func.value.id)
        if imported is not None:
            imported_module, imported_name = imported
            prefix = imported_module if imported_name is None else f"{imported_module}.{imported_name}"
            return prefix, node.func.attr
    return None


def _scan_source(source: str, module: str) -> BoundaryScan:
    tree = ast.parse(source)
    imports: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split(".")[0]] = (alias.name, None)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imports[alias.asname or alias.name] = (node.module, alias.name)

    callers: set[str] = set()
    violations: list[str] = []
    scopes: list[str] = []

    class Visitor(ast.NodeVisitor):
        def _visit_scope(self, node: ast.AST, name: str) -> None:
            scopes.append(name)
            self.generic_visit(node)
            scopes.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_scope(node, node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_scope(node, node.name)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._visit_scope(node, node.name)

        def visit_Call(self, node: ast.Call) -> None:
            target = _resolve_call(node, module, imports)
            if target in DECLARED_MODEL_TRANSPORTS and scopes:
                callers.add(f"{module}.{'/'.join(scopes)}")
            self.generic_visit(node)

        def visit_Constant(self, node: ast.Constant) -> None:
            if (
                isinstance(node.value, str)
                and module not in DECLARED_TRANSPORT_MODULES
                and any(marker in node.value for marker in MODEL_ENDPOINT_MARKERS)
            ):
                violations.append(
                    f"{module}:{node.lineno}: model endpoint outside a declared transport"
                )

    Visitor().visit(tree)

    if module not in DECLARED_TRANSPORT_MODULES:
        for imported_module, _ in imports.values():
            if any(
                imported_module == root or imported_module.startswith(f"{root}.")
                for root in MODEL_CLIENT_ROOTS
            ):
                violations.append(f"{module}: model client outside a declared transport")

    return BoundaryScan(frozenset(callers), tuple(sorted(set(violations))))


def _package_scan() -> BoundaryScan:
    callers: set[str] = set()
    violations: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        relative = path.relative_to(ROOT / "src").with_suffix("")
        module = ".".join(relative.parts)
        scan = _scan_source(path.read_text(encoding="utf-8"), module)
        callers.update(scan.callers)
        violations.extend(scan.violations)
    return BoundaryScan(frozenset(callers), tuple(sorted(violations)))


def test_declared_model_transport_inventory_matches_documented_callers() -> None:
    scan = _package_scan()
    assert not scan.violations
    assert scan.callers == {
        "bank_mcp.engines.dispute_agent._generate_refund_body",
        "bank_mcp.engines.dispute_agent._llm_lookup_contact",
        "bank_mcp.engines.llm_matcher.llm_extract_receipt",
        "bank_mcp.engines.llm_matcher.llm_match_merchants",
        "bank_mcp.report.delivery.narrate",
    }

    docs = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("README.md", "docs/ARCHITECTURE.md")
    )
    normalized = " ".join(docs.split())
    for boundary in (
        "summary for narration",
        "merchant-name lists for matching",
        "receipt email text",
        "merchant name for a best-guess",
        "refund draft",
    ):
        assert boundary in normalized


def test_boundary_scan_fails_closed_on_undeclared_model_transport() -> None:
    source = """
import anthropic
import urllib.request

MODEL_URL = "https://api.anthropic.com/v1/messages"

def bypass_declared_transport(prompt):
    return urllib.request.Request(MODEL_URL, data=prompt.encode())
"""
    scan = _scan_source(source, "bank_mcp.engines.new_feature")
    assert scan.callers == frozenset()
    assert set(scan.violations) == {
        "bank_mcp.engines.new_feature:5: model endpoint outside a declared transport",
        "bank_mcp.engines.new_feature: model client outside a declared transport",
    }


def test_boundary_scan_resolves_imported_transport_alias() -> None:
    source = """
from bank_mcp.report.delivery import call_haiku as invoke

def proposed_feature(system, user):
    return invoke(system, user)
"""
    scan = _scan_source(source, "bank_mcp.engines.new_feature")
    assert scan.callers == frozenset({"bank_mcp.engines.new_feature.proposed_feature"})
    assert not scan.violations

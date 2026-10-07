"""Servidor base para experimentar tools y esquemas generados por el SDK."""
from mcp.server import MCPServer

mcp = MCPServer("Laboratorio MCP mínimo")


@mcp.tool()
def health() -> dict[str, str]:
    """Indica que el proceso fue descubierto y puede responder."""
    return {"status": "ok", "message": "El servidor base está activo."}


@mcp.tool()
def add(a:int, b:int) -> int:
    """Suma dos números enteros."""
    # El SDK de MCP ya valida los tipos según las anotaciones antes de llamar a la función.
    # Si se desea validación defensiva, se lanza una excepción para respetar el contrato "-> int".
    return a + b


if __name__ == "__main__":
    # stdio es el transporte local por defecto; Inspector inicia este archivo
    # como subproceso y se comunica por sus streams.
    mcp.run()

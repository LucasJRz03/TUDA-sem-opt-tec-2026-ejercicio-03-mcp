"""Tools: capacidades que el host puede pedir ejecutar.

TRABAJO DEL ALUMNO: implementar las functions de este módulo
delegando en el service layer (``activities.services``).
No exponer SQL, ORM ni views como parte del contrato MCP.

La inscripción es una acción mutable, así que corre en dos pasos
(Approval Gate / human-in-the-loop):

1. ``request_registration_confirmation`` valida en el borde, consulta al
   dominio y emite un token opaco de un solo uso con TTL de 10 minutos.
2. ``register_for_activity`` sólo acepta ese token: lo consume (contra
   doble inscripción) y recién ahí delega en ``ActivityService.register``.

El modelo no puede inscribir directamente ni inventar un token válido:
sólo el servidor emite y reconoce ``confirmation_id``.
"""

import secrets
import time

from mcp.server import MCPServer

from activities.services import (
    Activity,
    ActivityFull,
    ActivityNotFound,
    AlreadyRegistered,
    InvalidEmail,
    get_activity_service,
)

MAX_QUERY_LENGTH = 100
MAX_EMAIL_LENGTH = 254
CONFIRMATION_TTL_SECONDS = 600  # 10 minutos
MAX_CONFIRMATION_ID_LENGTH = 128

# Tokens de confirmación pendientes, sólo en memoria (proceso stdio del
# servidor): {token: {"activity_id", "student_email", "expires_at"}}.
_pending_confirmations: dict[str, dict[str, object]] = {}


def _as_dict(activity: Activity) -> dict[str, object]:
    """Convierte el DTO del service layer en un dict apto para MCP.

    Evita exponer el objeto ORM o el service layer como contrato.
    Campos: id, title, available, seats.
    """
    return {
        "id": activity.id,
        "title": activity.title,
        "available": activity.available,
        "seats": activity.seats,
    }


def _normalize_query(query: str) -> str:
    """Limpia la query: recorta y limita la longitud a MAX_QUERY_LENGTH."""
    return str(query or "").strip()[:MAX_QUERY_LENGTH]


def _validate_activity_id(activity_id: object) -> str:
    """Valida la entrada ``activity_id`` en el borde MCP (antes del dominio).

    El service layer usa clave numérica, así que se exige un entero positivo.
    Lanza ``ValueError`` con un mensaje accionable.
    """
    value = str(activity_id or "").strip()
    if not value:
        raise ValueError('activity_id es obligatorio (ej. "1")')
    if not value.isdigit() or int(value) <= 0:
        raise ValueError(
            f"activity_id inválido: {value!r} (debe ser un entero positivo, ej. \"1\")"
        )
    return str(int(value))


def _validate_student_email(student_email: object) -> str:
    """Valida y normaliza ``student_email`` en el borde MCP (trim + minúsculas)."""
    value = str(student_email or "").strip().lower()
    if not value:
        raise ValueError("student_email es obligatorio (ej. alumno@untdf.edu.ar)")
    if len(value) > MAX_EMAIL_LENGTH or value.count("@") != 1:
        raise ValueError(f"student_email debe ser un correo válido: {value!r}")
    local, _, domain = value.partition("@")
    if not local or not domain or ".." in value or "." not in domain:
        raise ValueError(f"student_email debe ser un correo válido: {value!r}")
    return value


def _purge_expired_confirmations() -> None:
    """Descarta tokens vencidos del diccionario en memoria."""
    now = time.time()
    vencidos = [
        token
        for token, data in _pending_confirmations.items()
        if float(data["expires_at"]) <= now
    ]
    for token in vencidos:
        del _pending_confirmations[token]


def register_tools(mcp: MCPServer) -> None:
    @mcp.tool()
    def search_activities(query: str, only_available: bool = True) -> list[dict[str, object]]:
        """Busca actividades por título y, opcionalmente, sólo entre las disponibles."""
        service = get_activity_service()
        results = service.search(_normalize_query(query), only_available)
        return [_as_dict(activity) for activity in results]

    @mcp.tool()
    def request_registration_confirmation(
        activity_id: str, student_email: str
    ) -> dict[str, str]:
        """Paso 1 (Approval Gate): pide un token de confirmación para inscribir.

        Valida los argumentos en el borde, verifica contra el dominio que la
        actividad exista y emite un ``confirmation_id`` opaco de un solo uso
        con TTL de 10 minutos. No inscribe: devuelve el token para que el host
        lo muestre al humano y pida aprobación explícita. Recién después se
        puede llamar a ``register_for_activity`` con ese token.
        """
        try:
            activity_id = _validate_activity_id(activity_id)
            student_email = _validate_student_email(student_email)
        except ValueError as error:
            return {"status": "invalid_input", "message": str(error)}

        service = get_activity_service()
        try:
            activity = service.get_activity(activity_id)
        except ActivityNotFound:
            return {
                "status": "activity_not_found",
                "message": f"no existe la actividad `{activity_id}`; "
                "usá `search_activities` para elegir una actividad válida.",
            }
        except InvalidEmail as error:
            return {"status": "invalid_email", "message": str(error)}

        if not activity.available:
            return {
                "status": "activity_full",
                "message": f"la actividad `{activity.title}` no tiene cupos; "
                "elegí otra del catálogo antes de pedir confirmación.",
            }

        _purge_expired_confirmations()
        confirmation_id = secrets.token_urlsafe(32)
        _pending_confirmations[confirmation_id] = {
            "activity_id": activity_id,
            "student_email": student_email,
            "expires_at": time.time() + CONFIRMATION_TTL_SECONDS,
        }

        return {
            "status": "confirmation_required",
            "confirmation_id": confirmation_id,
            "activity_id": activity_id,
            "activity_title": activity.title,
            "student_email": student_email,
            "expires_in": "10 minutos",
            "message": f"Inscripción a `{activity.title}` para {student_email} "
            "pendiente de aprobación. Mostrá este token y el resumen al usuario "
            "y pedile confirmación explícita. Sólo si acepta, llamá a "
            "`register_for_activity` con este `confirmation_id`.",
        }

    @mcp.tool()
    def register_for_activity(confirmation_id: str) -> dict[str, str]:
        """Paso 2: ejecuta la inscripción con el token de confirmación.

        Única vía de inscripción. Requiere un ``confirmation_id`` válido,
        emitido por ``request_registration_confirmation`` y aprobado por el
        humano: si el token no existe, venció o ya se usó, devuelve un error
        accionable sin tocar el dominio. El token se consume antes de
        delegar en el service layer para evitar dobles inscripciones.
        """
        token = str(confirmation_id or "").strip()
        if not token:
            return {
                "status": "confirmation_required",
                "message": "falta `confirmation_id`: primero llamá a "
                "`request_registration_confirmation` y pedí aprobación explícita "
                "al usuario.",
            }
        if len(token) > MAX_CONFIRMATION_ID_LENGTH:
            return {
                "status": "invalid_confirmation",
                "message": "`confirmation_id` inválido: pedí uno nuevo con "
                "`request_registration_confirmation`.",
            }

        pending = _pending_confirmations.get(token)
        if pending is None:
            return {
                "status": "invalid_confirmation",
                "message": "`confirmation_id` inválido o ya utilizado. "
                "Solicitá uno nuevo con `request_registration_confirmation`.",
            }
        if float(pending["expires_at"]) <= time.time():
            del _pending_confirmations[token]
            return {
                "status": "confirmation_expired",
                "message": "La confirmación venció (TTL de 10 minutos). "
                "Solicitá una nueva con `request_registration_confirmation` "
                "y volvé a pedir aprobación al usuario.",
            }

        del _pending_confirmations[token]  # un solo uso: evita doble inscripción
        activity_id = str(pending["activity_id"])
        student_email = str(pending["student_email"])

        service = get_activity_service()
        try:
            return service.register(activity_id, student_email)
        except ActivityNotFound:
            return {
                "status": "activity_not_found",
                "message": f"no existe la actividad `{activity_id}`",
            }
        except InvalidEmail as error:
            return {"status": "invalid_email", "message": str(error)}
        except ActivityFull as error:
            return {
                "status": "activity_full",
                "message": f"{error} Pedí una nueva confirmación con "
                "`request_registration_confirmation` para otra actividad.",
            }
        except AlreadyRegistered as error:
            return {"status": "already_registered", "message": str(error)}

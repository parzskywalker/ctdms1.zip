"""CTDMS - Central Transportation Expense & Dispatch Management System."""

__version__ = "1.7.0"


def _register_safe_int_convertor() -> None:
    """Limit `{id:int}` path parameters to 18 digits.

    Starlette's default int convertor accepts any length, so /invoices/99999999999999999999 reached the database
    and crashed with OverflowError (HTTP 500). With this convertor such URLs simply do not match (HTTP 404).
    Must run before any router is defined, hence it lives in the package __init__.
    """
    from starlette.convertors import CONVERTOR_TYPES, IntegerConvertor

    class SafeIntConvertor(IntegerConvertor):
        regex = "[0-9]{1,18}"

    CONVERTOR_TYPES["int"] = SafeIntConvertor()


_register_safe_int_convertor()

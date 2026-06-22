from paw.db.base import Base


def test_naming_convention_present():
    # Alembic-friendly constraint naming so autogenerate/migrations are stable.
    nc = Base.metadata.naming_convention
    assert nc["pk"] == "pk_%(table_name)s"
    assert "fk" in nc and "ix" in nc

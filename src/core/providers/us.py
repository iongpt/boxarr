"""US box office provider (Box Office Mojo domestic)."""

from .bom import BoxOfficeMojoProvider


class USProvider(BoxOfficeMojoProvider):
    """Box office data provider for the United States (Box Office Mojo domestic)."""

    COUNTRY_CODE = "us"
    COUNTRY_NAME = "United States"
    AREA_CODE = None  # US domestic = no area parameter

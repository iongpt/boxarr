"""JSON data generator for weekly box office pages."""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..utils.atomic import atomic_write_json
from ..utils.config import configured_box_office_regions, settings
from ..utils.logger import get_logger
from .auto_add import _select_search_result
from .matcher import MatchResult
from .models import MovieStatus
from .radarr import RadarrService
from .region_charts import (
    chart_sort_key,
    lead_row,
    region_order,
    region_rows,
    row_codes,
)

logger = get_logger(__name__)

# Stored weekly files written since several regions can be tracked carry
# data_version 2, the regions, each region's status, and per movie its
# region rows. Files without "regions" are legacy (one region, unrecorded).
DATA_VERSION = 2


def _entry_rows(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return a stored movie entry's region rows."""
    rows = entry.get("regions")
    if rows:
        return [dict(row) for row in rows]
    return [
        {
            "code": entry.get("region") or "",
            "title": entry.get("title"),
            "rank": entry.get("rank"),
            "weekend_gross": entry.get("weekend_gross"),
            "total_gross": entry.get("total_gross"),
            "weeks": None,
            "theaters": None,
        }
    ]


def _combine_entries(
    first: Dict[str, Any], second: Dict[str, Any], order: Dict[str, int]
) -> Dict[str, Any]:
    """Merge two stored entries for the same film from different regions.

    The Radarr-linked entry (else the first) keeps its Radarr/TMDB fields;
    the region rows are joined and the chart fields follow the new lead.
    """
    base, other = (
        (second, first)
        if second.get("radarr_id") and not first.get("radarr_id")
        else (first, second)
    )
    rows = sorted(
        _entry_rows(first) + _entry_rows(second),
        key=lambda row: order.get(row.get("code") or "", len(order)),
    )
    lead = lead_row(rows, order)
    combined = dict(base)
    combined.update(
        {
            "rank": lead.get("rank"),
            "title": lead.get("title") or base.get("title"),
            "weekend_gross": lead.get("weekend_gross"),
            "total_gross": lead.get("total_gross"),
            "region": lead.get("code") or "",
            "regions": rows,
            "bom_imdb_id": base.get("bom_imdb_id") or other.get("bom_imdb_id"),
        }
    )
    return combined


def collapse_by_tmdb_id(
    entries: List[Dict[str, Any]], regions: Sequence[str]
) -> List[Dict[str, Any]]:
    """Merge entries from different regions that resolved to one TMDB film.

    An unmatched film is looked up on TMDB by title, so two regions' rows
    that could not be merged earlier (a localized title, no IMDb id) can
    resolve to the same film - or to a film another entry already links in
    Radarr. Left apart, both would later be linked to the same Radarr movie.
    Only entries covering different regions, at least one of them not in
    Radarr, are merged; a single region's chart is never changed.

    Args:
        entries: Stored movie entries in chart order
        regions: Area codes in configured order

    Returns:
        The entries, merged and re-sorted when anything was merged
    """
    order = region_order(regions)
    kept: List[Dict[str, Any]] = []
    by_tmdb_id: Dict[Any, List[int]] = {}
    collapsed = False

    for entry in entries:
        tmdb_id = entry.get("tmdb_id")
        if tmdb_id:
            codes = row_codes(_entry_rows(entry))
            target = next(
                (
                    index
                    for index in by_tmdb_id.get(tmdb_id, [])
                    if not (kept[index].get("radarr_id") and entry.get("radarr_id"))
                    and not (codes & row_codes(_entry_rows(kept[index])))
                ),
                None,
            )
            if target is not None:
                logger.info(
                    f"'{entry.get('title')}' and '{kept[target].get('title')}' "
                    f"are the same TMDB film ({tmdb_id}); merged them"
                )
                kept[target] = _combine_entries(kept[target], entry, order)
                collapsed = True
                continue
            by_tmdb_id.setdefault(tmdb_id, []).append(len(kept))
        kept.append(entry)

    if collapsed:
        kept.sort(
            key=lambda entry: chart_sort_key(
                entry.get("rank"), entry.get("region"), _entry_rows(entry), order
            )
        )
    return kept


class WeeklyDataGenerator:
    """Generates JSON data files for weekly box office data."""

    def __init__(self, radarr_service: Optional[RadarrService] = None):
        """
        Initialize data generator.

        Args:
            radarr_service: Optional Radarr service instance
        """
        self.radarr_service = radarr_service
        self.output_dir = settings.boxarr_data_directory / "weekly_pages"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def weekly_data_path(self, year: int, week: int) -> Path:
        """Return the stored weekly file of an ISO week."""
        return self.output_dir / f"{year}W{week:02d}.json"

    def generate_weekly_data(
        self,
        match_results: List[MatchResult],
        year: int,
        week: int,
        radarr_movies: Optional[List] = None,
        *,
        regions: Optional[Sequence[str]] = None,
        region_status: Optional[List[Dict[str, Any]]] = None,
    ) -> Path:
        """
        Generate JSON data file for a week's box office data.

        Args:
            match_results: Movie matching results
            year: Year
            week: Week number
            radarr_movies: Optional list of Radarr movies (for compatibility)
            regions: Area codes the week was built from, in configured
                order; defaults to the configured regions
            region_status: Each region's stored status entry

        Returns:
            Path to generated JSON file
        """
        metadata = self.build_weekly_data(
            match_results,
            year,
            week,
            regions=regions,
            region_status=region_status,
        )
        return self.write_weekly_data(metadata)

    def write_weekly_data(self, metadata: Dict[str, Any]) -> Path:
        """
        Write a week's data, built by build_weekly_data, to its file.

        Args:
            metadata: The week's data

        Returns:
            Path to the written JSON file
        """
        metadata_path = self.weekly_data_path(metadata["year"], metadata["week"])
        atomic_write_json(metadata_path, metadata, indent=2)

        logger.info(f"Generated weekly data: {metadata_path}")
        return metadata_path

    def build_weekly_data(  # noqa: C901
        self,
        match_results: List[MatchResult],
        year: int,
        week: int,
        *,
        regions: Optional[Sequence[str]] = None,
        region_status: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Build a week's stored data from its match results.

        Every key of a single-region week is kept as it was. Added: per
        movie ``region`` (its lead region), ``regions`` (one row per region
        it charted in) and ``bom_imdb_id`` (Box Office Mojo's IMDb id); per
        file ``data_version``, ``regions`` and ``region_status``.

        Args:
            match_results: Movie matching results, in chart order
            year: ISO year
            week: ISO week number
            regions: Area codes the week was built from, in configured
                order; defaults to the configured regions
            region_status: Each region's stored status entry

        Returns:
            The week's data
        """
        regions = (
            list(regions)
            if regions is not None
            else configured_box_office_regions(settings)
        )
        # Calculate friday and sunday from year and week
        from datetime import date, timedelta

        # Get the first day of the week (Monday)
        monday = date.fromisocalendar(year, week, 1)
        # Calculate Friday (4 days after Monday) and Sunday (6 days after Monday)
        friday = datetime.combine(monday + timedelta(days=4), datetime.min.time())
        sunday = datetime.combine(monday + timedelta(days=6), datetime.min.time())

        # Get quality profiles if available
        quality_profiles = {}
        ultra_hd_id = None

        if self.radarr_service:
            try:
                profiles = self.radarr_service.get_quality_profiles()
                quality_profiles = {p.id: p.name for p in profiles}

                # Find Ultra-HD profile
                for p in profiles:
                    if (
                        "ultra" in p.name.lower()
                        or "uhd" in p.name.lower()
                        or "2160" in p.name
                    ):
                        ultra_hd_id = p.id
                        break

                if not ultra_hd_id and settings.radarr_quality_profile_upgrade:
                    upgrade_profile = next(
                        (
                            p
                            for p in profiles
                            if p.name == settings.radarr_quality_profile_upgrade
                        ),
                        None,
                    )
                    if upgrade_profile:
                        ultra_hd_id = upgrade_profile.id

            except Exception as e:
                logger.warning(f"Could not fetch quality profiles: {e}")

        # Prepare movie data
        movies_data = []
        for result in match_results:
            movie_data = {
                "rank": result.box_office_movie.rank,
                "title": result.box_office_movie.title,
                "weekend_gross": result.box_office_movie.weekend_gross,
                "total_gross": result.box_office_movie.total_gross,
                "radarr_id": None,
                "radarr_title": None,
                "status": "Not in Radarr",
                "status_color": "#718096",
                "status_icon": "➕",
                "quality_profile_id": None,
                "quality_profile_name": None,
                "has_file": False,
                "can_upgrade_quality": False,
                "poster": None,
                "year": None,
                "genres": None,
                "overview": None,
                "imdb_id": None,
                "tmdb_id": None,
                "original_language": None,
            }

            if result.is_matched and result.radarr_movie:
                movie = result.radarr_movie
                movie_data.update(
                    {
                        "radarr_id": movie.id,
                        "radarr_title": movie.title,
                        "quality_profile_id": movie.qualityProfileId,
                        "quality_profile_name": quality_profiles.get(
                            movie.qualityProfileId, ""
                        ),
                        "has_file": movie.hasFile,
                        "year": movie.year,
                        "genres": ", ".join(movie.genres[:2]) if movie.genres else None,
                        "overview": (
                            movie.overview[:150] + "..."
                            if movie.overview and len(movie.overview) > 150
                            else movie.overview
                        ),
                        "imdb_id": movie.imdbId,
                        "tmdb_id": movie.tmdbId,
                        "original_language": movie.original_language,
                        "poster": movie.poster_url,
                        "can_upgrade_quality": bool(
                            movie.qualityProfileId
                            and ultra_hd_id
                            and movie.qualityProfileId != ultra_hd_id
                            and settings.boxarr_features_quality_upgrade
                        ),
                    }
                )

                # Initial status (will be updated dynamically when page loads)
                if movie.hasFile:
                    movie_data["status"] = "Downloaded"
                    movie_data["status_color"] = "#48bb78"
                    movie_data["status_icon"] = "✅"
                elif movie.status == MovieStatus.RELEASED and movie.isAvailable:
                    movie_data["status"] = "Missing"
                    movie_data["status_color"] = "#f56565"
                    movie_data["status_icon"] = "❌"
                elif movie.status == MovieStatus.IN_CINEMAS:
                    movie_data["status"] = "In Cinemas"
                    movie_data["status_color"] = "#f6ad55"
                    movie_data["status_icon"] = "🎬"
                else:
                    movie_data["status"] = "Pending"
                    movie_data["status_color"] = "#ed8936"
                    movie_data["status_icon"] = "⏳"
            else:
                # For unmatched movies, ALWAYS try to get data from TMDB
                # This ensures we have poster and description for dashboard display
                if self.radarr_service:
                    try:
                        # Search for movie in TMDB via Radarr
                        search_results = self.radarr_service.search_movie(
                            result.box_office_movie.title
                        )
                        if search_results and len(search_results) > 0:
                            # Prefer the result carrying Box Office Mojo's
                            # IMDb id; without one, the first result.
                            tmdb_movie = _select_search_result(
                                search_results, result.box_office_movie
                            )
                            movie_data.update(
                                {
                                    "tmdb_id": tmdb_movie.get("tmdbId"),
                                    "year": tmdb_movie.get("year"),
                                    "overview": (
                                        tmdb_movie.get("overview", "")[:150] + "..."
                                        if tmdb_movie.get("overview")
                                        and len(tmdb_movie.get("overview", "")) > 150
                                        else tmdb_movie.get("overview")
                                    ),
                                    "poster": tmdb_movie.get("remotePoster"),
                                    "imdb_id": tmdb_movie.get("imdbId"),
                                    "genres": (
                                        ", ".join(tmdb_movie.get("genres", [])[:2])
                                        if tmdb_movie.get("genres")
                                        else None
                                    ),
                                    "original_language": (
                                        tmdb_movie.get("originalLanguage", {}).get(
                                            "name"
                                        )
                                        if isinstance(
                                            tmdb_movie.get("originalLanguage"),
                                            dict,
                                        )
                                        else None
                                    ),
                                }
                            )
                            logger.info(
                                f"Enriched '{result.box_office_movie.title}' with TMDB data"
                            )
                    except Exception as e:
                        logger.warning(
                            f"Could not fetch TMDB data for '{result.box_office_movie.title}': {e}"
                        )

            # Region keys go last so every older key keeps its place.
            box_office_movie = result.box_office_movie
            movie_data["region"] = box_office_movie.region or ""
            movie_data["regions"] = region_rows(box_office_movie)
            movie_data["bom_imdb_id"] = box_office_movie.imdb_id

            movies_data.append(movie_data)

        movies_data = collapse_by_tmdb_id(movies_data, regions)

        # Save metadata with full movie data
        metadata = {
            "generated_at": datetime.now().isoformat(),
            "year": year,
            "week": week,
            "friday": friday.isoformat(),
            "sunday": sunday.isoformat(),
            # One entry per film, however many regions it charted in.
            "total_movies": len(movies_data),
            "matched_movies": sum(1 for m in movies_data if m["radarr_id"]),
            "quality_profiles": quality_profiles,
            "ultra_hd_id": ultra_hd_id,
            "movies": movies_data,  # Store full movie data for display
            "data_version": DATA_VERSION,
            "regions": regions,
            "region_status": list(region_status or []),
        }
        return metadata

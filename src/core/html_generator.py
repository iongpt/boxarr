"""Static HTML generator for weekly box office pages."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..utils.config import settings
from ..utils.logger import get_logger
from .matcher import MatchResult
from .radarr import QualityProfile, RadarrService

logger = get_logger(__name__)


class WeeklyPageGenerator:
    """Generates static HTML pages for weekly box office data."""

    def __init__(self, radarr_service: Optional[RadarrService] = None):
        """
        Initialize page generator.

        Args:
            radarr_service: Optional Radarr service instance
        """
        self.radarr_service = radarr_service
        self.output_dir = settings.boxarr_data_directory / "weekly_pages"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_weekly_page(  # noqa: C901
        self,
        match_results: List[MatchResult],
        year: int,
        week: int,
        radarr_movies: Optional[List] = None,
    ) -> Path:
        """
        Generate static HTML page for a week's box office data.

        Args:
            match_results: Movie matching results
            year: Year
            week: Week number
            radarr_movies: Optional list of Radarr movies (for compatibility)

        Returns:
            Path to generated HTML file
        """
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
                        "poster": movie.poster_url,
                        "can_upgrade_quality": bool(
                            movie.qualityProfileId
                            and ultra_hd_id
                            and movie.qualityProfileId != ultra_hd_id
                            and settings.boxarr_features_quality_upgrade
                        ),
                    }
                )

                # Initial status (will be updated dynamically)
                if movie.hasFile:
                    movie_data["status"] = "Downloaded"
                    movie_data["status_color"] = "#48bb78"
                    movie_data["status_icon"] = "✅"
                elif movie.status == "released" and movie.isAvailable:
                    movie_data["status"] = "Missing"
                    movie_data["status_color"] = "#f56565"
                    movie_data["status_icon"] = "❌"
                elif movie.status == "inCinemas":
                    movie_data["status"] = "In Cinemas"
                    movie_data["status_color"] = "#f6ad55"
                    movie_data["status_icon"] = "🎬"
                else:
                    movie_data["status"] = "Pending"
                    movie_data["status_color"] = "#ed8936"
                    movie_data["status_icon"] = "⏳"
            else:
                # For unmatched movies, try to get data from TMDB
                if self.radarr_service:
                    try:
                        # Search for movie in TMDB via Radarr
                        search_results = self.radarr_service.search_movie(
                            result.box_office_movie.title
                        )
                        if search_results and len(search_results) > 0:
                            # Use the first result
                            tmdb_movie = search_results[0]
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
                                }
                            )
                    except Exception as e:
                        logger.warning(
                            f"Could not fetch TMDB data for '{result.box_office_movie.title}': {e}"
                        )

            movies_data.append(movie_data)

        # Generate HTML
        html = self._generate_html(
            movies_data, year, week, friday, sunday, quality_profiles, ultra_hd_id
        )

        # Save HTML file
        filename = f"{year}W{week:02d}.html"
        output_path = self.output_dir / filename

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        # Also save as current if it's the latest week
        current_week = datetime.now().isocalendar()[1]
        current_year = datetime.now().year
        if year == current_year and week == current_week:
            current_path = self.output_dir / "current.html"
            with open(current_path, "w", encoding="utf-8") as f:
                f.write(html)

        # Save metadata with full movie data
        metadata = {
            "generated_at": datetime.now().isoformat(),
            "year": year,
            "week": week,
            "friday": friday.isoformat(),
            "sunday": sunday.isoformat(),
            "total_movies": len(movies_data),
            "matched_movies": sum(1 for m in movies_data if m["radarr_id"]),
            "quality_profiles": quality_profiles,
            "ultra_hd_id": ultra_hd_id,
            "movies": movies_data,  # Store full movie data for regeneration
        }

        metadata_path = self.output_dir / f"{year}W{week:02d}.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(f"Generated weekly page: {output_path}")
        return output_path

    def _generate_html(
        self,
        movies: List[Dict],
        year: int,
        week: int,
        friday: datetime,
        sunday: datetime,
        quality_profiles: Dict[int, str],
        ultra_hd_id: Optional[int],
    ) -> str:
        """Generate the HTML content."""

        # Generate HTML with compact header and dynamic navigation
        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Box Office Top 10 - Week {week}, {year}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea, #764ba2);
            min-height: 100vh;
            padding: 15px;
        }}
        .container {{ max-width: 2400px; margin: 0 auto; }}

        /* Compact header with integrated navigation */
        .header-nav {{
            background: rgba(255,255,255,0.98);
            border-radius: 12px;
            padding: 15px 20px;
            margin: 0 15px 20px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.15);
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 15px;
        }}

        .header-left {{
            display: flex;
            align-items: center;
            gap: 20px;
            flex: 1;
            min-width: 300px;
        }}

        .title-group {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}

        .page-title {{
            font-size: 1.5em;
            font-weight: bold;
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .date-info {{
            font-size: 0.85em;
            color: #718096;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .week-badge {{
            background: #f7fafc;
            color: #667eea;
            padding: 2px 8px;
            border-radius: 4px;
            font-weight: 600;
            font-size: 0.9em;
        }}

        .connection-status {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 4px 10px;
            background: #f7fafc;
            border-radius: 20px;
            font-size: 0.85em;
        }}
        .status-dot {{
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #cbd5e0;
        }}
        .status-dot.connected {{ background: #48bb78; }}
        .status-dot.disconnected {{ background: #f56565; }}

        .nav-section {{
            display: flex;
            align-items: center;
            gap: 10px;
        }}

        .dashboard-btn {{
            padding: 8px 16px;
            background: #667eea;
            color: white;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.85em;
            transition: all 0.3s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
        }}
        .dashboard-btn:hover {{
            background: #5a67d8;
            transform: translateY(-1px);
            box-shadow: 0 4px 8px rgba(102, 126, 234, 0.2);
        }}

        .week-nav {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}

        .week-nav-label {{
            color: #4a5568;
            font-weight: 600;
            font-size: 0.85em;
            margin-right: 4px;
        }}

        .week-link {{
            padding: 6px 10px;
            background: white;
            color: #667eea;
            text-decoration: none;
            border-radius: 6px;
            border: 1.5px solid #e2e8f0;
            transition: all 0.2s;
            font-weight: 500;
            font-size: 0.8em;
            white-space: nowrap;
        }}
        .week-link:hover {{
            background: #667eea;
            color: white;
            border-color: #667eea;
            transform: translateY(-1px);
        }}
        .week-link.current {{
            background: #667eea;
            color: white;
            border-color: #764ba2;
        }}

        .week-dropdown {{
            padding: 6px 10px;
            background: white;
            color: #667eea;
            border: 1.5px solid #e2e8f0;
            border-radius: 6px;
            font-size: 0.8em;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .week-dropdown:hover {{
            background: #f7fafc;
            border-color: #667eea;
        }}

        /* Mobile responsive header */
        @media (max-width: 1024px) {{
            .header-nav {{
                flex-direction: column;
                align-items: stretch;
            }}
            .header-left {{
                width: 100%;
                justify-content: space-between;
            }}
            .nav-section {{
                width: 100%;
                flex-direction: column;
                gap: 10px;
            }}
            .dashboard-btn {{
                width: 100%;
                justify-content: center;
            }}
            .week-nav {{
                width: 100%;
                overflow-x: auto;
                padding-bottom: 5px;
            }}
        }}

        /* Grid for 5 movies per row on 4K */
        .movies-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }}

        @media (min-width: 2560px) {{
            .movies-grid {{ grid-template-columns: repeat(5, 1fr); }}
        }}

        .movie-card {{
            background: white;
            border-radius: 15px;
            overflow: hidden;
            box-shadow: 0 10px 30px rgba(0,0,0,0.15);
            transition: all 0.3s;
            display: flex;
            flex-direction: column;
            height: 100%;
            position: relative;
        }}
        .movie-card.updating {{
            animation: pulse 1s infinite;
        }}
        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.7; }}
        }}
        .movie-card:hover {{
            transform: translateY(-8px) scale(1.02);
            box-shadow: 0 20px 50px rgba(0,0,0,0.25);
        }}

        .movie-poster-container {{
            position: relative;
            padding-top: 120%;
            background: linear-gradient(135deg, #667eea, #764ba2);
            overflow: hidden;
        }}
        .movie-poster {{
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            object-fit: cover;
        }}
        .movie-rank {{
            position: absolute;
            top: 8px;
            left: 8px;
            background: rgba(255,255,255,0.95);
            color: #667eea;
            font-size: 1.4em;
            font-weight: bold;
            width: 40px;
            height: 40px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 4px 12px rgba(0,0,0,0.25);
        }}

        .movie-content {{
            padding: 18px;
            display: flex;
            flex-direction: column;
            flex-grow: 1;
        }}

        .movie-title {{
            font-size: 1.1em;
            font-weight: bold;
            color: #2d3748;
            margin-bottom: 8px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}

        .movie-status {{
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 6px 10px;
            border-radius: 6px;
            font-weight: 600;
            margin-bottom: 8px;
            font-size: 0.85em;
            transition: all 0.3s;
        }}

        .quality-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: #f7fafc;
            padding: 8px 10px;
            border-radius: 6px;
            margin-bottom: 10px;
            min-height: 36px;
        }}
        .quality-text {{
            font-size: 0.85em;
            color: #4a5568;
            font-weight: 600;
        }}
        .upgrade-btn {{
            background: linear-gradient(135deg, #9f7aea, #805ad5);
            color: white;
            border: none;
            padding: 4px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.75em;
            font-weight: 600;
            transition: all 0.2s;
        }}
        .upgrade-btn:hover {{
            transform: scale(1.1);
            box-shadow: 0 2px 8px rgba(159,122,234,0.4);
        }}
        .upgrade-btn:disabled {{
            background: #cbd5e0;
            cursor: not-allowed;
        }}

        .add-btn {{
            background: linear-gradient(135deg, #48bb78, #38a169);
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.85em;
            font-weight: 600;
            transition: all 0.2s;
            width: 100%;
        }}
        .add-btn:hover {{
            transform: scale(1.02);
            box-shadow: 0 4px 12px rgba(72,187,120,0.4);
        }}
        .add-btn:disabled {{
            background: #cbd5e0;
            cursor: not-allowed;
        }}

        .movie-details {{
            flex-grow: 1;
            margin-bottom: 12px;
        }}
        .movie-year {{
            font-weight: 600;
            color: #4a5568;
            font-size: 0.85em;
            margin-bottom: 4px;
        }}
        .movie-genre {{
            color: #718096;
            font-size: 0.8em;
            margin-bottom: 6px;
        }}
        .movie-plot {{
            color: #718096;
            font-size: 0.8em;
            line-height: 1.4;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}

        .movie-links {{
            display: flex;
            gap: 8px;
            margin-top: auto;
        }}
        .movie-link {{
            flex: 1;
            padding: 8px;
            text-align: center;
            text-decoration: none;
            border-radius: 6px;
            font-weight: 600;
            transition: all 0.3s;
            font-size: 0.85em;
        }}
        .imdb-link {{ background: #f5c518; color: black; }}
        .wiki-link {{ background: #4a5568; color: white; }}
        .movie-link:hover {{
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        }}

        .no-poster {{
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 3em;
            color: white;
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
        }}

        .footer {{
            text-align: center;
            margin-top: 40px;
            color: white;
            font-size: 0.85em;
            opacity: 0.8;
        }}

        .notification {{
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 15px 20px;
            background: white;
            border-radius: 8px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
            display: none;
            z-index: 1000;
        }}
        .notification.show {{ display: block; }}
        .notification.success {{ border-left: 4px solid #48bb78; }}
        .notification.error {{ border-left: 4px solid #f56565; }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Compact header with integrated navigation -->
        <div class="header-nav">
            <div class="header-left">
                <div class="title-group">
                    <div class="page-title">
                        🎬 Box Office Top 10
                        <span class="connection-status" id="connectionStatus">
                            <span class="status-dot"></span>
                            <span class="status-text">Checking...</span>
                        </span>
                    </div>
                    <div class="date-info">
                        {friday.strftime('%B %d')} - {sunday.strftime('%B %d, %Y')}
                        <span class="week-badge">W{week}/{year % 100}</span>
                    </div>
                </div>
            </div>

            <div class="nav-section">
                <a href="/dashboard" class="dashboard-btn">← Dashboard</a>
                <div class="week-nav" id="weekNav">
                    <span class="week-nav-label">Loading...</span>
                </div>
            </div>
        </div>

        <div class="content-wrapper" style="padding: 0 15px;">
            <div class="movies-grid" id="moviesGrid">
"""

        # Add movie cards
        for movie in movies:
            poster_html = (
                f'<img src="{movie["poster"]}" alt="{movie["title"]}" class="movie-poster">'
                if movie.get("poster")
                else '<div class="no-poster">🎬</div>'
            )

            # Status styling (will be updated dynamically)
            status_style = f"background-color: {movie['status_color']}20; color: {movie['status_color']}; border: 2px solid {movie['status_color']};"

            # IMDb link
            imdb_url = (
                f"https://www.imdb.com/title/{movie['imdb_id']}/"
                if movie.get("imdb_id")
                else f"https://www.imdb.com/find?q={movie['title']}"
            )

            html += f"""
            <div class="movie-card" data-radarr-id="{movie['radarr_id'] or ''}" data-rank="{movie['rank']}">
                <div class="movie-poster-container">
                    {poster_html}
                    <div class="movie-rank">#{movie['rank']}</div>
                </div>
                <div class="movie-content">
                    <h3 class="movie-title" title="{movie['title']}">{movie['title']}</h3>
                    <div class="movie-status" data-status="{movie['status']}" style="{status_style}">
                        <span class="status-icon">{movie['status_icon']}</span>
                        <span class="status-text">{movie['status']}</span>
                    </div>
"""

            # Quality row or Add to Radarr button (will be updated dynamically)
            if movie["quality_profile_name"]:
                upgrade_btn = ""
                if movie["can_upgrade_quality"] and movie["radarr_id"] and ultra_hd_id:
                    upgrade_btn = f"""<button class="upgrade-btn" data-movie-id="{movie['radarr_id']}" data-profile-id="{ultra_hd_id}" onclick="upgradeQuality(this)">⬆️ Ultra-HD</button>"""

                html += f"""
                    <div class="quality-row">
                        <span class="quality-text">Profile: <span class="profile-name">{movie['quality_profile_name']}</span></span>
                        {upgrade_btn}
                    </div>
"""
            elif movie["status"] == "Not in Radarr":
                # Show Add to Radarr button for unmatched movies
                html += f"""
                    <div class="quality-row">
                        <button class="add-btn" data-movie-title="{movie['title']}" onclick="addToRadarr(this)">➕ Add to Radarr</button>
                    </div>
"""

            # Movie details
            html += '                    <div class="movie-details">\n'
            if movie.get("year"):
                html += f'                        <div class="movie-year">Year: {movie["year"]}</div>\n'
            if movie.get("genres"):
                html += f'                        <div class="movie-genre">{movie["genres"]}</div>\n'
            if movie.get("overview"):
                html += f'                        <div class="movie-plot">{movie["overview"]}</div>\n'
            html += "                    </div>\n"

            # Links
            html += f"""                    <div class="movie-links">
                        <a href="{imdb_url}" target="_blank" class="movie-link imdb-link">IMDb</a>
                        <a href="https://en.wikipedia.org/w/index.php?search={movie['title']} film" target="_blank" class="movie-link wiki-link">Wiki</a>
                    </div>
"""

            html += """                </div>
            </div>
"""

        html += f"""        </div>

        <div class="footer">
            <p>🤖 Generated by Boxarr • Updates every Tuesday at 11 PM</p>
            <p>Data from Box Office Mojo • Connected to Radarr</p>
        </div>
    </div>

    <div class="notification" id="notification">
        <span id="notificationText"></span>
    </div>

    <script>
    // Configuration from page
    const pageData = {{
        year: {year},
        week: {week},
        week_str: "{year}W{week:02d}",
        movies: {json.dumps([m['radarr_id'] for m in movies if m['radarr_id']])}
    }};

    // Load dynamic navigation
    async function loadNavigation() {{
        const navContainer = document.getElementById('weekNav');

        try {{
            const response = await fetch('/api/weeks');
            const data = await response.json();

            if (data.weeks && data.weeks.length > 0) {{
                let navHTML = '<span class="week-nav-label">Weeks:</span>';

                // Show only the most recent 4 weeks as buttons
                const recentWeeks = data.weeks.slice(0, 4);
                recentWeeks.forEach(week => {{
                    const isCurrent = week.week_str === pageData.week_str;
                    navHTML += `<a href="/${{week.week_str}}.html" class="week-link ${{isCurrent ? 'current' : ''}}">W${{week.week}}/${{week.year % 100}}</a>`;
                }});

                // Add comprehensive dropdown for all weeks (including recent ones)
                if (data.weeks.length > 1) {{
                    navHTML += `
                        <select class="week-dropdown" onchange="if(this.value) window.location.href=this.value">
                            <option value="">All weeks (${{data.weeks.length}})</option>`;

                    // Group weeks by year for better organization
                    let currentYear = null;
                    data.weeks.forEach((week, index) => {{
                        if (week.year !== currentYear) {{
                            if (currentYear !== null) {{
                                navHTML += '</optgroup>';
                            }}
                            navHTML += `<optgroup label="${{week.year}}">`;
                            currentYear = week.year;
                        }}
                        const selected = week.week_str === pageData.week_str ? ' selected' : '';
                        navHTML += `<option value="/${{week.week_str}}.html"${{selected}}>Week ${{week.week}} - ${{week.date_range || 'No date'}}</option>`;
                    }});

                    if (currentYear !== null) {{
                        navHTML += '</optgroup>';
                    }}

                    navHTML += '</select>';
                }}

                navContainer.innerHTML = navHTML;
            }} else {{
                navContainer.innerHTML = '<span class="week-nav-label">No weeks available</span>';
            }}
        }} catch (e) {{
            console.error('Failed to load navigation:', e);
            navContainer.innerHTML = '<span class="week-nav-label">Navigation unavailable</span>';
        }}
    }}

    // Check connection and update status
    async function checkConnection() {{
        const statusDot = document.querySelector('.status-dot');
        const statusText = document.querySelector('.status-text');

        try {{
            const response = await fetch('/api/health');
            if (response.ok) {{
                const data = await response.json();
                if (data.radarr_connected) {{
                    statusDot.classList.add('connected');
                    statusDot.classList.remove('disconnected');
                    statusText.textContent = 'Connected';
                    return true;
                }}
            }}
        }} catch (e) {{
            console.error('Connection check failed:', e);
        }}

        statusDot.classList.add('disconnected');
        statusDot.classList.remove('connected');
        statusText.textContent = 'Disconnected';
        return false;
    }}

    // Update movie statuses dynamically
    async function updateMovieStatuses() {{
        // Filter out null IDs (movies not in Radarr)
        const validMovieIds = pageData.movies.filter(id => id !== null);
        if (!validMovieIds.length) return;

        try {{
            // Get current status for all movies
            const response = await fetch('/api/movies/status', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ movie_ids: validMovieIds }})
            }});

            if (!response.ok) return;

            const statuses = await response.json();

            // Update each movie card
            statuses.forEach(movie => {{
                const card = document.querySelector(`[data-radarr-id="${{movie.id}}"]`);
                if (!card) return;

                // Update status
                const statusEl = card.querySelector('.movie-status');
                const statusIcon = statusEl.querySelector('.status-icon');
                const statusText = statusEl.querySelector('.status-text');

                statusText.textContent = movie.status;
                statusIcon.textContent = movie.status_icon;
                statusEl.style.backgroundColor = `${{movie.status_color}}20`;
                statusEl.style.color = movie.status_color;
                statusEl.style.borderColor = movie.status_color;

                // Update quality profile
                const profileName = card.querySelector('.profile-name');
                if (profileName && movie.quality_profile) {{
                    profileName.textContent = movie.quality_profile;
                }}

                // Update upgrade button
                const upgradeBtn = card.querySelector('.upgrade-btn');
                if (upgradeBtn) {{
                    upgradeBtn.disabled = !movie.can_upgrade;
                }}
            }});
        }} catch (e) {{
            console.error('Failed to update statuses:', e);
        }}
    }}

    // Upgrade quality profile
    async function upgradeQuality(button) {{
        const movieId = button.dataset.movieId;
        const profileId = button.dataset.profileId;

        button.disabled = true;
        button.textContent = 'Upgrading...';

        try {{
            const response = await fetch(`/api/movies/${{movieId}}/upgrade`, {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{
                    movie_id: parseInt(movieId),
                    quality_profile_id: parseInt(profileId)
                }})
            }});

            const result = await response.json();

            if (result.success) {{
                showNotification('Quality profile upgraded successfully!', 'success');
                button.textContent = '✅';
                button.disabled = true;

                // Update profile name
                const card = button.closest('.movie-card');
                const profileName = card.querySelector('.profile-name');
                if (profileName && result.new_profile) {{
                    profileName.textContent = result.new_profile;
                }}
            }} else {{
                showNotification('Upgrade failed: ' + result.message, 'error');
                button.textContent = '⬆️ Ultra-HD';
                button.disabled = false;
            }}
        }} catch (e) {{
            showNotification('Connection error', 'error');
            button.textContent = '⬆️ Ultra-HD';
            button.disabled = false;
        }}
    }}

    // Add movie to Radarr
    async function addToRadarr(button) {{
        const movieTitle = button.dataset.movieTitle;

        if (!confirm(`Add "${{movieTitle}}" to Radarr?`)) {{
            return;
        }}

        button.disabled = true;
        button.textContent = 'Adding...';

        try {{
            const response = await fetch('/api/movies/add', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ movie_title: movieTitle }})
            }});

            const result = await response.json();

            if (result.success) {{
                showNotification(result.message, 'success');
                button.textContent = '✅ Added';
                button.disabled = true;

                // Reload page after allowing time for regeneration
                if (result.regenerated_weeks && result.regenerated_weeks.length > 0) {{
                    showNotification(`Updating ${{result.regenerated_weeks.length}} weeks...`, 'success');
                }}

                setTimeout(() => {{
                    location.reload();
                }}, 3000);  // Give time for backend regeneration
            }} else {{
                showNotification('Failed to add: ' + result.message, 'error');
                button.textContent = '➕ Add to Radarr';
                button.disabled = false;
            }}
        }} catch (e) {{
            showNotification('Connection error: ' + e, 'error');
            button.textContent = '➕ Add to Radarr';
            button.disabled = false;
        }}
    }}

    // Show notification
    function showNotification(message, type) {{
        const notification = document.getElementById('notification');
        const text = document.getElementById('notificationText');

        text.textContent = message;
        notification.className = `notification ${{type}} show`;

        setTimeout(() => {{
            notification.classList.remove('show');
        }}, 3000);
    }}

    // Initialize on load
    document.addEventListener('DOMContentLoaded', async () => {{
        // Load navigation immediately
        await loadNavigation();

        // Check connection and update movie statuses
        const connected = await checkConnection();
        if (connected) {{
            await updateMovieStatuses();
            // Update every 30 seconds
            setInterval(updateMovieStatuses, 30000);
        }}
    }});

    // Check connection every 10 seconds
    setInterval(checkConnection, 10000);

    // Reload navigation every 5 minutes (in case new weeks are added)
    setInterval(loadNavigation, 300000);
    </script>
</body>
</html>
"""

        return html

    def get_available_weeks(self) -> List[str]:
        """Get list of available week pages."""
        weeks = []

        for file in sorted(self.output_dir.glob("????W??.html"), reverse=True):
            if file.name != "current.html":
                week_str = file.stem
                weeks.append(week_str)

        return weeks

    def regenerate_weeks_with_movie(self, movie_title: str) -> List[str]:
        """
        Find and regenerate all weeks containing a specific movie.

        Args:
            movie_title: Title of the movie to search for

        Returns:
            List of regenerated week identifiers
        """
        regenerated = []

        # Search through all metadata files
        for metadata_file in self.output_dir.glob("????W??.json"):
            try:
                with open(metadata_file) as f:
                    metadata = json.load(f)

                # Check if this week contains the movie
                movies = metadata.get("movies", [])
                contains_movie = any(
                    movie.get("title", "").lower() == movie_title.lower()
                    for movie in movies
                )

                if contains_movie:
                    logger.info(
                        f"Regenerating week {metadata_file.stem} containing '{movie_title}'"
                    )

                    # Rebuild match results from metadata
                    from .boxoffice import BoxOfficeMovie
                    from .matcher import MatchResult

                    # Get current Radarr movies
                    radarr_movies = []
                    if self.radarr_service:
                        radarr_movies = self.radarr_service.get_all_movies()

                    # Create match results from stored data
                    match_results = []
                    for movie_data in movies:
                        # Create box office movie
                        bo_movie = BoxOfficeMovie(
                            rank=movie_data["rank"],
                            title=movie_data["title"],
                            weekend_gross=movie_data.get("weekend_gross"),
                            total_gross=movie_data.get("total_gross"),
                        )

                        # Try to find matching Radarr movie
                        radarr_movie = None
                        for rm in radarr_movies:
                            if rm.title.lower() == movie_data["title"].lower():
                                radarr_movie = rm
                                break

                        # Create match result
                        match_result = MatchResult(
                            box_office_movie=bo_movie,
                            radarr_movie=radarr_movie,
                            confidence=1.0 if radarr_movie else 0.0,
                        )
                        match_results.append(match_result)

                    # Regenerate the page
                    year = metadata["year"]
                    week = metadata["week"]
                    # No longer need friday/sunday from metadata, they'll be calculated
                    self.generate_weekly_page(match_results, year, week)
                    regenerated.append(f"{year}W{week:02d}")

            except Exception as e:
                logger.error(f"Error processing {metadata_file}: {e}")

        return regenerated

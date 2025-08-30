#!/usr/bin/env python3
"""Diagnostic script to check Boxarr scheduler configuration and status."""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import settings
from src.core.scheduler import BoxarrScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz


def check_configuration():
    """Check basic configuration."""
    print("\n" + "="*60)
    print("CONFIGURATION CHECK")
    print("="*60)
    
    print(f"✓ Radarr configured: {settings.is_configured}")
    print(f"✓ Radarr URL: {settings.radarr_url}")
    print(f"✓ Radarr API Key: {'***' + settings.radarr_api_key[-4:] if settings.radarr_api_key else 'NOT SET'}")
    print(f"✓ Scheduler enabled: {settings.boxarr_scheduler_enabled}")
    print(f"✓ Cron expression: {settings.boxarr_scheduler_cron}")
    print(f"✓ Timezone: {settings.boxarr_scheduler_timezone}")
    print(f"✓ Auto-add movies: {settings.boxarr_features_auto_add}")
    
    return settings.is_configured and settings.boxarr_scheduler_enabled


def parse_cron_schedule():
    """Parse and explain the cron schedule."""
    print("\n" + "="*60)
    print("SCHEDULE ANALYSIS")
    print("="*60)
    
    cron = settings.boxarr_scheduler_cron
    print(f"Cron expression: {cron}")
    
    try:
        # Parse cron expression
        trigger = CronTrigger.from_crontab(cron, timezone=settings.boxarr_scheduler_timezone)
        
        # Get next 5 run times
        print(f"\nNext 5 scheduled runs (in {settings.boxarr_scheduler_timezone}):")
        next_time = datetime.now(pytz.timezone(settings.boxarr_scheduler_timezone))
        
        for i in range(5):
            next_time = trigger.get_next_fire_time(None, next_time)
            if next_time:
                local_time = next_time.astimezone()
                print(f"  {i+1}. {next_time.strftime('%Y-%m-%d %H:%M:%S %Z')} ({local_time.strftime('%Y-%m-%d %H:%M:%S %Z')})")
                next_time += timedelta(seconds=1)
        
        # Parse cron parts
        parts = cron.split()
        if len(parts) >= 5:
            minute, hour, day_of_month, month, day_of_week = parts[:5]
            
            days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
            
            print(f"\nSchedule breakdown:")
            print(f"  Minute: {minute}")
            print(f"  Hour: {hour}")
            print(f"  Day of month: {day_of_month}")
            print(f"  Month: {month}")
            print(f"  Day of week: {day_of_week} ({days[int(day_of_week)] if day_of_week.isdigit() else day_of_week})")
            
            if day_of_week.isdigit():
                print(f"\n📅 Runs every {days[int(day_of_week)]} at {hour}:{minute.zfill(2)}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error parsing cron expression: {e}")
        print("\nValid cron format: 'minute hour day_of_month month day_of_week'")
        print("Examples:")
        print("  0 23 * * 2  - Every Tuesday at 23:00 (11 PM)")
        print("  0 20 * * 5  - Every Friday at 20:00 (8 PM)")
        print("  30 14 * * 1 - Every Monday at 14:30 (2:30 PM)")
        return False


def check_scheduler_instance():
    """Test creating a scheduler instance."""
    print("\n" + "="*60)
    print("SCHEDULER INSTANCE TEST")
    print("="*60)
    
    try:
        from src.core.boxoffice import BoxOfficeService
        from src.core.radarr import RadarrService
        
        print("Creating scheduler instance...")
        scheduler = BoxarrScheduler(
            boxoffice_service=BoxOfficeService(),
            radarr_service=RadarrService() if settings.radarr_api_key else None,
        )
        
        print("✓ Scheduler instance created successfully")
        
        # Check if scheduler can be started
        print("Testing scheduler startup...")
        scheduler.scheduler.add_job(
            lambda: print("Test job executed"),
            CronTrigger.from_crontab(settings.boxarr_scheduler_cron),
            id="test_job",
            name="Test Job",
            replace_existing=True,
        )
        
        jobs = scheduler.scheduler.get_jobs()
        print(f"✓ Test job added successfully. Total jobs: {len(jobs)}")
        
        # Start scheduler temporarily to get next run times
        scheduler.scheduler.start()
        
        for job in scheduler.scheduler.get_jobs():
            print(f"  Job: {job.name} (ID: {job.id})")
            if hasattr(job, 'next_run_time') and job.next_run_time:
                print(f"    Next run: {job.next_run_time}")
            else:
                print(f"    Next run: Not scheduled yet")
        
        # Get next run time
        next_run = scheduler.get_next_run_time()
        if next_run:
            print(f"\n📅 Next scheduled run: {next_run}")
            time_until = next_run - datetime.now(next_run.tzinfo)
            hours = time_until.total_seconds() / 3600
            print(f"   Time until next run: {hours:.1f} hours")
        
        scheduler.scheduler.shutdown(wait=False)
        return True
        
    except Exception as e:
        print(f"❌ Error creating scheduler: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_history():
    """Check scheduler run history."""
    print("\n" + "="*60)
    print("SCHEDULER HISTORY")
    print("="*60)
    
    history_dir = Path(settings.boxarr_data_directory) / "history"
    
    if not history_dir.exists():
        print("⚠️  No history directory found - scheduler may not have run yet")
        return False
    
    history_files = sorted(history_dir.glob("*.json"), reverse=True)[:5]
    
    if not history_files:
        print("⚠️  No history files found - scheduler has not completed any runs")
        return False
    
    print(f"Found {len(history_files)} recent run(s):")
    
    import json
    for file_path in history_files:
        try:
            with open(file_path) as f:
                data = json.load(f)
            
            timestamp = data.get("timestamp", "Unknown")
            matched = data.get("matched_count", 0)
            total = data.get("total_count", 0)
            
            # Parse timestamp
            if timestamp != "Unknown":
                run_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                time_ago = datetime.now(run_time.tzinfo) - run_time
                days = time_ago.days
                hours = time_ago.seconds // 3600
                
                print(f"\n  ✓ {run_time.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"    {days} days, {hours} hours ago")
                print(f"    Matched {matched}/{total} movies")
            
        except Exception as e:
            print(f"  ❌ Error reading {file_path.name}: {e}")
    
    return len(history_files) > 0


def check_logs():
    """Check for scheduler-related log entries."""
    print("\n" + "="*60)
    print("RECENT SCHEDULER LOGS")
    print("="*60)
    
    log_file = Path(settings.boxarr_data_directory) / "logs" / "boxarr.log"
    
    if not log_file.exists():
        print("⚠️  No log file found")
        return False
    
    print(f"Checking {log_file}...")
    
    scheduler_logs = []
    with open(log_file, 'r') as f:
        for line in f:
            if any(keyword in line.lower() for keyword in ["scheduler", "cron", "job", "apscheduler"]):
                scheduler_logs.append(line.strip())
    
    if scheduler_logs:
        print(f"\nFound {len(scheduler_logs)} scheduler-related log entries")
        print("Last 10 entries:")
        for log in scheduler_logs[-10:]:
            print(f"  {log}")
    else:
        print("⚠️  No scheduler-related logs found")
    
    return True


def suggest_fixes():
    """Suggest fixes based on findings."""
    print("\n" + "="*60)
    print("RECOMMENDATIONS")
    print("="*60)
    
    if not settings.is_configured:
        print("❌ Radarr is not configured. Configure it via the web UI at http://localhost:8888/setup")
    
    if not settings.boxarr_scheduler_enabled:
        print("❌ Scheduler is disabled. Enable it in settings:")
        print("   1. Go to http://localhost:8888/setup")
        print("   2. Check 'Enable Scheduler'")
        print("   3. Save settings")
    
    # Check timezone
    import tzlocal
    try:
        local_tz = tzlocal.get_localzone()
        if str(local_tz) != settings.boxarr_scheduler_timezone:
            print(f"\n⚠️  Timezone mismatch:")
            print(f"   System timezone: {local_tz}")
            print(f"   Scheduler timezone: {settings.boxarr_scheduler_timezone}")
            print("   Consider updating the scheduler timezone in settings")
    except:
        pass
    
    print("\n📝 To manually trigger an update:")
    print("   curl -X POST http://localhost:8888/api/scheduler/trigger")
    
    print("\n📝 To check scheduler status via API:")
    print("   curl http://localhost:8888/api/health")
    
    print("\n📝 To view scheduler history:")
    print("   curl http://localhost:8888/api/scheduler/history")


def main():
    """Run all checks."""
    print("\n" + "="*60)
    print("BOXARR SCHEDULER DIAGNOSTIC")
    print("="*60)
    print(f"Current time: {datetime.now()}")
    print(f"Config directory: {settings.boxarr_data_directory}")
    
    # Run checks
    config_ok = check_configuration()
    cron_ok = parse_cron_schedule() if config_ok else False
    scheduler_ok = check_scheduler_instance() if config_ok else False
    check_history()
    check_logs()
    
    # Show recommendations
    suggest_fixes()
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    if config_ok and cron_ok and scheduler_ok:
        print("✅ Scheduler appears to be configured correctly")
        print("   If it's still not triggering, check:")
        print("   1. The application is running continuously")
        print("   2. The system time is correct")
        print("   3. The logs for any error messages")
    else:
        print("❌ Issues found with scheduler configuration")
        print("   Please address the recommendations above")


if __name__ == "__main__":
    main()
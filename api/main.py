import os
import sys
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from api.logging_config import setup_logging

# Configure logging
setup_logging()
logger = logging.getLogger(__name__)

# Configure watchfiles logger to show file paths
watchfiles_logger = logging.getLogger("watchfiles.main")
watchfiles_logger.setLevel(logging.DEBUG)  # Enable DEBUG to see file paths

# Add the current directory to the path so we can import the api package
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Apply watchfiles monkey patch BEFORE uvicorn import
is_development = os.environ.get("NODE_ENV") != "production"
if is_development:
    import watchfiles
    current_dir = os.path.dirname(os.path.abspath(__file__))
    logs_dir = os.path.join(current_dir, "logs")
    
    original_watch = watchfiles.watch
    def patched_watch(*args, **kwargs):
        # Only watch the api directory but exclude logs subdirectory
        # Instead of watching the entire api directory, watch specific subdirectories
        api_subdirs = []
        for item in os.listdir(current_dir):
            item_path = os.path.join(current_dir, item)
            if os.path.isdir(item_path) and item != "logs":
                api_subdirs.append(item_path)
            elif os.path.isfile(item_path) and item.endswith(".py"):
                api_subdirs.append(item_path)
        
        return original_watch(*api_subdirs, **kwargs)
    watchfiles.watch = patched_watch

import uvicorn

# Check for required environment variables
required_env_vars = ['GOOGLE_API_KEY', 'OPENAI_API_KEY']
missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
if missing_vars:
    logger.warning(f"Missing environment variables: {', '.join(missing_vars)}")
    logger.warning("Some functionality may not work correctly without these variables.")

# Configure Google Generative AI
import google.generativeai as genai
from api.config import GOOGLE_API_KEY

if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
else:
    logger.warning("GOOGLE_API_KEY not configured")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DeepWiki API Server")
    parser.add_argument("--batch-index", action="store_true", help="Run batch indexer for GitLab groups and exit")
    args = parser.parse_args()

    if args.batch_index:
        # Run batch indexer mode
        import asyncio
        from api.batch_indexer import main as batch_main
        logger.info("Running in batch-index mode")
        asyncio.run(batch_main())
        sys.exit(0)

    # Get port from environment variable or use default
    port = int(os.environ.get("PORT", 8001))

    # Import the app here to ensure environment variables are set first
    from api.api import app

    # Optional: set up scheduled batch indexing
    from api.config import BATCH_INDEX_SCHEDULE
    if BATCH_INDEX_SCHEDULE:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger

            scheduler = AsyncIOScheduler()

            async def _scheduled_batch_index():
                from api.batch_indexer import BatchIndexer
                from api.config import GITLAB_BATCH_GROUPS, GITLAB_SERVICE_TOKEN, GITLAB_URL
                group_ids = [int(g.strip()) for g in GITLAB_BATCH_GROUPS.split(",") if g.strip()]
                if group_ids and GITLAB_SERVICE_TOKEN and GITLAB_URL:
                    indexer = BatchIndexer(GITLAB_URL, GITLAB_SERVICE_TOKEN, group_ids)
                    await indexer.run()

            scheduler.add_job(_scheduled_batch_index, CronTrigger.from_crontab(BATCH_INDEX_SCHEDULE))
            scheduler.start()
            logger.info(f"Scheduled batch indexing with cron: {BATCH_INDEX_SCHEDULE}")
        except Exception as e:
            logger.warning(f"Failed to set up scheduled batch indexing: {e}")

    logger.info(f"Starting Streaming API on port {port}")

    # Run the FastAPI app with uvicorn
    uvicorn.run(
        "api.api:app",
        host="0.0.0.0",
        port=port,
        reload=is_development,
        reload_excludes=["**/logs/*", "**/__pycache__/*", "**/*.pyc"] if is_development else None,
    )

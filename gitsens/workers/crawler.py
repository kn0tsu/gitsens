import os
import redis
from rq import Queue
import json # For parsing gh search output

from gitsens.config import (
    REDIS_HOST, REDIS_PORT, REDIS_DB_ANALYZER, ANALYZER_QUEUE_NAME, 
    REDIS_DB_CRAWLER, CRAWLER_QUEUE_NAME, 
    MAX_REPOS_PER_ORG
)
from gitsens.utils import run_command


def discover_repos_from_org_list_job(org_names_list):
    """
    takes a list of organization names, lists their repos, 
    and enqueues them for analysis by the analyzer worker.
    """

    print(f"[Crawler] Processing organization list: {org_names_list}")
    redis_conn_analyzer = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB_ANALYZER)
    analyzer_q = Queue(ANALYZER_QUEUE_NAME, connection=redis_conn_analyzer)
    
    enqueued_count = 0
    for org_name in org_names_list:
        print(f"[Crawler] Listing repos for organization: {org_name}")
        list_repo_cmd = [
            "gh", "repo", "list", org_name,
            "-L", str(MAX_REPOS_PER_ORG), 
            "--json", "nameWithOwner", 
            "--jq", ".[].nameWithOwner"
        ]
        repos_result = run_command(list_repo_cmd)

        if not repos_result or repos_result.returncode != 0 or not repos_result.stdout:
            print(f"[Crawler] Could not list repos for {org_name}. 'gh' tool installed and logged in?")
            continue

        repo_full_names = [name for name in repos_result.stdout.strip().split('\n') if name and '/' in name]
        print(f"[Crawler] Found {len(repo_full_names)} repos for {org_name}.")

        for full_name in repo_full_names:
            try:
                org, repo = full_name.split('/', 1)
                print(f"[Crawler] Enqueuing for ANALYSIS: {org}/{repo}")
                analyzer_q.enqueue(
                    'gitsens.workers.analyzer.analyze_repository_job',
                    org, repo, job_timeout='3h' 
                )
                enqueued_count += 1
            except ValueError:
                print(f"[Crawler] Skipping invalid repo full name format: {full_name}")
                continue
                
    return f"Crawled {len(org_names_list)} organizations, enqueued {enqueued_count} repos for analysis."


def discover_repos_from_gh_search_job(gh_search_query, limit=100):

    """
    uses 'gh search repos' and enqueues found repos for analysis by the Analyzer Worker.
    """

    print(f"[Crawler] Running GitHub search: {gh_search_query} with limit {limit}")
    redis_conn_analyzer = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB_ANALYZER)
    analyzer_q = Queue(ANALYZER_QUEUE_NAME, connection=redis_conn_analyzer)

    search_cmd = [
        "gh", "search", "repos",
        "--limit", str(limit),
        "--json", "nameWithOwner",
        "--jq", ".items[].nameWithOwner", 
        gh_search_query
    ]
    
    print(f"[Crawler] Executing command: {' '.join(search_cmd)}")
    search_result = run_command(search_cmd)

    if not search_result or search_result.returncode != 0 or not search_result.stdout:
        print(f"[Crawler] GitHub search failed or returned no results for query: {gh_search_query}")
        if search_result and search_result.stderr:
            print(f"[Crawler] Stderr: {search_result.stderr.strip()}")
        return f"GitHub search failed for query: {gh_search_query}"

    repo_full_names = [name for name in search_result.stdout.strip().split('\n') if name and '/' in name]
    print(f"[Crawler] Found {len(repo_full_names)} repos from search query.")
    
    enqueued_count = 0
    for full_name in repo_full_names:
        try:
            org, repo = full_name.split('/', 1)
            print(f"[Crawler] Enqueuing for ANALYSIS: {org}/{repo}")
            analyzer_q.enqueue(
                'gitsens.workers.analyzer.analyze_repository_job',
                org, repo, job_timeout='3h' # increased timeout for full pipeline
            )
            enqueued_count +=1
        except ValueError:
            print(f"[Crawler] Skipping invalid repo full name format from search: {full_name}")
            continue
            
    return f"GitHub search processed. Enqueued {enqueued_count} repos for analysis from query: {gh_search_query}"
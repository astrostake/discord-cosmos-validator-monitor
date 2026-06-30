# utils/retry.py
# -*- coding: utf-8 -*-
"""Retry utilities for resilient API calls with exponential backoff."""

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


async def api_get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    timeout: Optional[float] = None,
) -> httpx.Response:
    """Perform an HTTP GET request with exponential backoff retry.

    Retries on network errors, timeouts, 5xx server errors, and 429 rate limits.
    Does NOT retry on 4xx client errors (except 429).

    Args:
        client: The httpx async client to use.
        url: The URL to fetch.
        max_retries: Maximum number of attempts.
        backoff_base: Base for exponential backoff calculation.
        timeout: Optional per-request timeout override.

    Returns:
        The successful httpx.Response.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            kwargs = {}
            if timeout is not None:
                kwargs['timeout'] = timeout
            response = await client.get(url, **kwargs)
            response.raise_for_status()
            return response

        except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as e:
            last_exception = e

            # Don't retry on 4xx client errors (except 429 rate limit)
            if isinstance(e, httpx.HTTPStatusError):
                status_code = e.response.status_code
                if 400 <= status_code < 500 and status_code != 429:
                    raise

            if attempt < max_retries - 1:
                wait_time = backoff_base ** attempt

                # Rate limit: respect Retry-After header if available
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                    retry_after = e.response.headers.get('Retry-After')
                    if retry_after:
                        try:
                            wait_time = max(float(retry_after), wait_time)
                        except ValueError:
                            pass
                    logger.warning(
                        f"Rate limited on {url}, retrying in {wait_time:.1f}s..."
                    )
                else:
                    logger.warning(
                        f"Request to {url} failed (attempt {attempt + 1}/{max_retries}): "
                        f"{type(e).__name__}: {e}. Retrying in {wait_time:.1f}s..."
                    )

                await asyncio.sleep(wait_time)
            else:
                logger.error(f"All {max_retries} attempts failed for {url}: {e}")

    raise last_exception

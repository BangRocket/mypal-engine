#!/usr/bin/env python3
"""Performance benchmark for Clara Memory System.

Measures latency for memory operations with and without caching.
Run this script before and after Phase 5 optimizations to compare.

Usage:
    # Basic benchmark
    python scripts/benchmark_memory.py

    # With more iterations
    python scripts/benchmark_memory.py --iterations 50

    # Warm cache mode (run twice to see cache benefit)
    python scripts/benchmark_memory.py --warm-cache

    # Compare with cache disabled
    python scripts/benchmark_memory.py --no-cache
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.WARNING,  # Quiet by default
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class BenchmarkResult:
    """Result of a single benchmark operation."""

    operation: str
    times_ms: list[float] = field(default_factory=list)

    @property
    def mean(self) -> float:
        return statistics.mean(self.times_ms) if self.times_ms else 0

    @property
    def median(self) -> float:
        return statistics.median(self.times_ms) if self.times_ms else 0

    @property
    def min(self) -> float:
        return min(self.times_ms) if self.times_ms else 0

    @property
    def max(self) -> float:
        return max(self.times_ms) if self.times_ms else 0

    @property
    def stdev(self) -> float:
        return statistics.stdev(self.times_ms) if len(self.times_ms) > 1 else 0


def time_operation(func, *args, **kwargs) -> tuple[float, any]:
    """Time an operation and return (elapsed_ms, result)."""
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms, result


def benchmark_embedding(iterations: int, text: str = "Hello, how are you today?") -> BenchmarkResult:
    """Benchmark embedding generation."""
    from mypalclara.core.memory.embeddings.base import BaseEmbedderConfig
    from mypalclara.core.memory.embeddings.openai import OpenAIEmbedding

    result = BenchmarkResult(operation="embedding")

    # Create embedder (with or without cache based on env)
    embedder = OpenAIEmbedding(
        BaseEmbedderConfig(
            model="text-embedding-3-small",
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    )

    for i in range(iterations):
        # Vary the text slightly to test cache misses vs hits
        test_text = f"{text} (iteration {i})" if i > 0 else text
        elapsed_ms, _ = time_operation(embedder.embed, test_text)
        result.times_ms.append(elapsed_ms)

    return result


def benchmark_embedding_cache_hit(iterations: int) -> BenchmarkResult:
    """Benchmark embedding with guaranteed cache hits."""
    from mypalclara.core.memory.embeddings.base import BaseEmbedderConfig
    from mypalclara.core.memory.embeddings.openai import OpenAIEmbedding

    result = BenchmarkResult(operation="embedding_cache_hit")

    embedder = OpenAIEmbedding(
        BaseEmbedderConfig(
            model="text-embedding-3-small",
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    )

    # Same text every time to guarantee cache hits after first
    text = "This is a consistent benchmark text for cache testing."

    for i in range(iterations):
        elapsed_ms, _ = time_operation(embedder.embed, text)
        result.times_ms.append(elapsed_ms)

    return result


def benchmark_key_memories(iterations: int, user_id: str = "benchmark-user") -> BenchmarkResult:
    """Benchmark key memories retrieval."""
    from mypalclara.core.memory import ROOK

    result = BenchmarkResult(operation="key_memories")

    if ROOK is None:
        logger.warning("ROOK not available, skipping key_memories benchmark")
        return result

    for _ in range(iterations):
        elapsed_ms, _ = time_operation(
            ROOK.get_all,
            user_id=user_id,
            agent_id="clara",
            filters={"is_key": "true"},
            limit=15,
        )
        result.times_ms.append(elapsed_ms)

    return result


def benchmark_user_search(iterations: int, user_id: str = "benchmark-user") -> BenchmarkResult:
    """Benchmark user memory search."""
    from mypalclara.core.memory import ROOK

    result = BenchmarkResult(operation="user_search")

    if ROOK is None:
        logger.warning("ROOK not available, skipping user_search benchmark")
        return result

    queries = [
        "What do you know about me?",
        "Tell me about my preferences",
        "What projects am I working on?",
        "Do you remember our last conversation?",
        "What are my interests?",
    ]

    for i in range(iterations):
        query = queries[i % len(queries)]
        elapsed_ms, _ = time_operation(
            ROOK.search,
            query,
            user_id=user_id,
            agent_id="clara",
        )
        result.times_ms.append(elapsed_ms)

    return result


def benchmark_project_search(
    iterations: int,
    user_id: str = "benchmark-user",
    project_id: str = "benchmark-project",
) -> BenchmarkResult:
    """Benchmark project memory search."""
    from mypalclara.core.memory import ROOK

    result = BenchmarkResult(operation="project_search")

    if ROOK is None:
        logger.warning("ROOK not available, skipping project_search benchmark")
        return result

    queries = [
        "project status",
        "recent changes",
        "todo items",
        "bug fixes",
        "feature requests",
    ]

    for i in range(iterations):
        query = queries[i % len(queries)]
        elapsed_ms, _ = time_operation(
            ROOK.search,
            query,
            user_id=user_id,
            agent_id="clara",
            filters={"project_id": project_id},
        )
        result.times_ms.append(elapsed_ms)

    return result


def benchmark_fetch_mem0_context(
    iterations: int,
    user_id: str = "benchmark-user",
    project_id: str = "benchmark-project",
) -> BenchmarkResult:
    """Benchmark the full fetch_mem0_context operation."""
    from mypalclara.core.memory_manager import MemoryManager

    result = BenchmarkResult(operation="fetch_mem0_context")

    try:
        manager = MemoryManager.get_instance()
    except RuntimeError:
        # Initialize with a dummy LLM
        manager = MemoryManager.initialize(lambda msgs: "ok")

    messages = [
        "Hello, how are you today?",
        "What do you remember about me?",
        "Can you help me with my project?",
        "Tell me about our previous conversations",
        "What are my preferences?",
    ]

    for i in range(iterations):
        message = messages[i % len(messages)]
        elapsed_ms, _ = time_operation(
            manager.fetch_mem0_context,
            user_id=user_id,
            project_id=project_id,
            user_message=message,
        )
        result.times_ms.append(elapsed_ms)

    return result


def benchmark_fsrs_ranking(iterations: int, user_id: str = "benchmark-user") -> BenchmarkResult:
    """Benchmark FSRS ranking (batched vs individual)."""
    from mypalclara.core.memory_manager import MemoryManager

    result = BenchmarkResult(operation="fsrs_ranking")

    try:
        manager = MemoryManager.get_instance()
    except RuntimeError:
        manager = MemoryManager.initialize(lambda msgs: "ok")

    # Create mock results
    mock_results = [{"id": f"mem-{i}", "memory": f"Test memory {i}", "score": 0.8 - i * 0.05} for i in range(20)]

    for _ in range(iterations):
        elapsed_ms, _ = time_operation(
            manager._rank_results_with_fsrs_batch,
            mock_results,
            user_id,
        )
        result.times_ms.append(elapsed_ms)

    return result


def print_results(results: list[BenchmarkResult], title: str = "Benchmark Results"):
    """Print benchmark results in a table format."""
    print(f"\n{'='*70}")
    print(f" {title}")
    print(f"{'='*70}")
    print(f"{'Operation':<25} {'Mean':>10} {'Median':>10} {'Min':>10} {'Max':>10} {'StdDev':>10}")
    print(f"{'-'*70}")

    for r in results:
        if r.times_ms:
            print(
                f"{r.operation:<25} "
                f"{r.mean:>9.1f}ms "
                f"{r.median:>9.1f}ms "
                f"{r.min:>9.1f}ms "
                f"{r.max:>9.1f}ms "
                f"{r.stdev:>9.1f}ms"
            )
        else:
            print(f"{r.operation:<25} {'N/A':>10} {'(skipped)':>10}")

    print(f"{'='*70}")

    # Calculate total
    total_mean = sum(r.mean for r in results if r.times_ms)
    print(f"{'TOTAL (sequential)':<25} {total_mean:>9.1f}ms")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="Benchmark Clara Memory System")
    parser.add_argument(
        "--iterations",
        type=int,
        default=10,
        help="Number of iterations per operation (default: 10)",
    )
    parser.add_argument(
        "--warm-cache",
        action="store_true",
        help="Run warmup pass before benchmarking",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable caching for comparison",
    )
    parser.add_argument(
        "--user-id",
        default="benchmark-user",
        help="User ID for memory operations",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    # Disable cache if requested
    if args.no_cache:
        os.environ["MEMORY_EMBEDDING_CACHE"] = "false"
        os.environ["REDIS_URL"] = ""
        print("Running with cache DISABLED")
    else:
        print(f"Running with cache {'ENABLED' if os.getenv('REDIS_URL') else 'DISABLED (no REDIS_URL)'}")

    print(f"Iterations: {args.iterations}")
    print(f"User ID: {args.user_id}")
    print(f"Timestamp: {datetime.now().isoformat()}")

    # Warmup if requested
    if args.warm_cache:
        print("\nRunning warmup pass...")
        benchmark_embedding_cache_hit(3)
        benchmark_key_memories(3, args.user_id)
        benchmark_user_search(3, args.user_id)
        print("Warmup complete.\n")

    # Run benchmarks
    results = []

    print("Running benchmarks...")

    # Embedding benchmarks
    print("  - Embedding (varied text)...")
    results.append(benchmark_embedding(args.iterations))

    print("  - Embedding (cache hit test)...")
    results.append(benchmark_embedding_cache_hit(args.iterations))

    # Memory retrieval benchmarks
    print("  - Key memories...")
    results.append(benchmark_key_memories(args.iterations, args.user_id))

    print("  - User search...")
    results.append(benchmark_user_search(args.iterations, args.user_id))

    print("  - Project search...")
    results.append(benchmark_project_search(args.iterations, args.user_id))

    # Full context fetch
    print("  - Full fetch_mem0_context...")
    results.append(benchmark_fetch_mem0_context(args.iterations, args.user_id))

    # FSRS ranking
    print("  - FSRS ranking (batched)...")
    results.append(benchmark_fsrs_ranking(args.iterations, args.user_id))

    # Print results
    cache_status = "WITH CACHE" if os.getenv("REDIS_URL") and not args.no_cache else "WITHOUT CACHE"
    print_results(results, f"Benchmark Results ({cache_status})")

    # Performance expectations from the plan
    print("Expected Performance (from plan):")
    print("  - Embedding (cold): ~150ms, (warm): ~5ms")
    print("  - Key memories (cold): ~100ms, (warm): ~10ms")
    print("  - User/Project search (cold): ~200ms, (warm): ~10ms")
    print("  - Total fetch_mem0_context (cold): ~250ms, (warm): ~25ms")


if __name__ == "__main__":
    main()

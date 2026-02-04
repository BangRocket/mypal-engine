# Migration Guide: Phase 5 Performance Optimizations

This guide covers migrating to the Phase 5 release which includes significant performance improvements to Clara's memory system.

## What's New

### Performance Improvements

| Operation | Before | After (Cold) | After (Warm) |
|-----------|--------|--------------|--------------|
| Embedding | ~150ms | ~150ms | **~5ms** |
| Key memories | ~100ms | ~100ms | **~10ms** |
| User search | ~200ms | ~200ms | **~10ms** |
| Project search | ~200ms | ~200ms | **~10ms** |
| **Total fetch** | **~650ms** | **~250ms** | **~25ms** |

### New Features

1. **Redis Caching** - Optional caching layer for embeddings, search results, and key memories
2. **Self-Hosted Qdrant** - Support for self-hosted Qdrant as an alternative to pgvector
3. **Parallel Memory Fetches** - Concurrent fetching of key, user, and project memories
4. **Batched FSRS Lookups** - Single database query instead of N queries for FSRS ranking
5. **Blue-Green Migration** - Zero-downtime migration tools for pgvector → Qdrant

---

## Quick Start (Minimal Changes)

If you just want the parallel fetch improvements without Redis or Qdrant:

```bash
# No configuration needed - parallel fetches are automatic
# Your existing setup will work with improved performance
```

**Expected improvement:** ~650ms → ~250ms (parallel fetches only)

---

## Full Setup with Redis Caching

### Step 1: Add Redis

**Docker Compose:**
```bash
docker-compose --profile redis up -d redis
```

**Or standalone:**
```bash
docker run -d --name clara-redis -p 6379:6379 redis:7-alpine
```

### Step 2: Configure Environment

Add to your `.env`:
```bash
# Redis cache URL
REDIS_URL=redis://localhost:6379/0

# Enable embedding cache (default: true)
MEMORY_EMBEDDING_CACHE=true
```

**For Docker Compose**, the services already have these variables configured. Just set:
```bash
REDIS_URL=redis://redis:6379/0
```

### Step 3: Verify

Check logs for:
```
INFO clara.memory.config - Embedding cache: ENABLED (Redis)
INFO clara.memory.cache - Redis cache connected
```

**Expected improvement:** ~250ms → ~25ms (warm cache)

---

## Migrating from pgvector to Qdrant

This section covers migrating your vector store from PostgreSQL/pgvector to self-hosted Qdrant.

### Why Migrate?

| Aspect | pgvector | Qdrant |
|--------|----------|--------|
| Purpose | General-purpose DB extension | Purpose-built vector DB |
| Performance | Good | Better for vector operations |
| Scaling | Vertical | Horizontal |
| Memory usage | Higher | Optimized for vectors |
| Filtering | SQL-based | Native payload filtering |

### Migration Strategy: Blue-Green with Dual-Write

Zero-downtime migration with rollback capability:

```
Phase 1: Dual-Write    → Writes to both, reads from pgvector
Phase 2: Dual-Read     → Writes to both, reads from both (validation)
Phase 3: Cutover       → Writes to both, reads from Qdrant
Phase 4: Cleanup       → Writes to Qdrant only
```

### Step-by-Step Migration

#### 1. Deploy Qdrant

**Docker Compose:**
```bash
docker-compose --profile qdrant up -d qdrant
```

**Or standalone:**
```bash
docker run -d --name clara-qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v qdrant_data:/qdrant/storage \
  qdrant/qdrant:latest
```

#### 2. Run Migration Script

```bash
# Dry run first
python scripts/migrate_pgvector_to_qdrant.py --dry-run

# Run migration with progress
python scripts/migrate_pgvector_to_qdrant.py --batch-size 100

# If interrupted, resume from checkpoint
python scripts/migrate_pgvector_to_qdrant.py --resume
```

#### 3. Validate Migration

```bash
# Compare search results between stores
python scripts/validate_migration.py --sample-size 1000
```

Expected output:
```
VALIDATION RESULTS
==================================================
pgvector records: 5000
Qdrant records: 5000
Sample size: 1000
Found in Qdrant: 998
Missing: 2
Match rate: 99.8%

VERDICT: PASS - Migration is ready for cutover
```

#### 4. Enable Dual-Write Mode

```bash
# .env
VECTOR_STORE_MODE=dual_write
QDRANT_URL=http://localhost:6333
```

Restart your application. Monitor logs for:
```
INFO clara.memory.vector.dual_write - DualWriteVectorStore initialized in dual_write mode
```

#### 5. Monitor for 24-48 Hours

Watch for secondary write failures:
```bash
grep "Secondary.*failed" /var/log/clara.log
```

#### 6. Enable Dual-Read Mode (Validation)

```bash
VECTOR_STORE_MODE=dual_read
```

This compares results from both stores and logs mismatches:
```
WARNING - search result mismatch: overlap=8/10 (80.0%)
```

#### 7. Cutover to Qdrant

```bash
VECTOR_STORE_MODE=secondary_only
QDRANT_URL=http://localhost:6333
```

Now all reads come from Qdrant.

#### 8. Cleanup (After 1 Week)

Once confident, remove pgvector:

```bash
# Remove dual-write (optional - keeps pgvector as backup)
# Or unset MEM0_DATABASE_URL to stop using pgvector entirely
```

### Rollback Procedure

At any point, revert to pgvector:

```bash
# Emergency rollback
VECTOR_STORE_MODE=primary_only
# Restart application
```

---

## Environment Variables Reference

### Redis Cache

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | (none) | Redis connection URL. If not set, caching is disabled. |
| `MEMORY_EMBEDDING_CACHE` | `true` | Enable/disable embedding cache |

### Qdrant

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_URL` | (none) | Qdrant server URL. Takes priority over pgvector. |
| `QDRANT_API_KEY` | (none) | API key for Qdrant Cloud or secured instances |

### Migration

| Variable | Default | Description |
|----------|---------|-------------|
| `VECTOR_STORE_MODE` | `primary_only` | Migration mode: `primary_only`, `dual_write`, `dual_read`, `secondary_only` |

### Priority Order for Vector Store

1. `QDRANT_URL` (self-hosted Qdrant) - **Recommended for production**
2. `MEM0_DATABASE_URL` (pgvector) - Legacy production option
3. Local Qdrant (`qdrant_data/`) - Development only

---

## Docker Compose Profiles

New profiles available:

```bash
# Redis only
docker-compose --profile redis up -d

# Qdrant only
docker-compose --profile qdrant up -d

# Full stack with Redis + Qdrant
docker-compose --profile discord --profile redis --profile qdrant up -d
```

---

## Benchmarking

Compare performance before and after:

```bash
# Without cache
python scripts/benchmark_memory.py --no-cache --iterations 20

# With cache (cold)
python scripts/benchmark_memory.py --iterations 20

# With cache (warm)
python scripts/benchmark_memory.py --warm-cache --iterations 20
```

Example output:
```
======================================================================
 Benchmark Results (WITH CACHE)
======================================================================
Operation                      Mean     Median        Min        Max     StdDev
----------------------------------------------------------------------
embedding                   152.3ms    151.2ms    148.1ms    160.2ms      4.1ms
embedding_cache_hit           4.2ms      3.8ms      3.1ms      8.2ms      1.5ms
key_memories                 12.3ms     11.8ms     10.2ms     18.1ms      2.3ms
user_search                  15.1ms     14.2ms     12.1ms     22.3ms      3.1ms
project_search               14.8ms     13.9ms     11.8ms     21.2ms      2.9ms
fetch_mem0_context           28.3ms     26.1ms     22.4ms     45.2ms      6.8ms
fsrs_ranking                  2.1ms      1.9ms      1.5ms      4.2ms      0.8ms
======================================================================
TOTAL (sequential)           229.1ms
======================================================================
```

---

## Troubleshooting

### Redis Connection Failed

```
WARNING clara.memory.cache - Redis unavailable: Connection refused
```

**Solution:** Check Redis is running and `REDIS_URL` is correct.

### Qdrant Collection Not Found

```
ERROR - Collection clara_memories not found
```

**Solution:** Run the migration script to create the collection:
```bash
python scripts/migrate_pgvector_to_qdrant.py --dry-run
```

### High Cache Miss Rate

Check embedding cache stats:
```python
from clara_core.memory.cache import RedisCache
cache = RedisCache.get_instance()
print(cache.get_stats())
```

If hits are low, check:
- Redis is running
- `MEMORY_EMBEDDING_CACHE=true`
- TTL hasn't expired (default: 24h for embeddings)

### Migration Validation Failures

```
VERDICT: FAIL - Review mismatches before cutover
```

**Common causes:**
1. Records not fully migrated - resume migration
2. Index configuration differences - check Qdrant indexes
3. Data changed during migration - re-run migration

---

## Rollback Checklist

If you need to rollback:

1. **Rollback Redis Cache:**
   ```bash
   MEMORY_EMBEDDING_CACHE=false
   # or
   unset REDIS_URL
   ```

2. **Rollback Qdrant Migration:**
   ```bash
   VECTOR_STORE_MODE=primary_only
   unset QDRANT_URL
   ```

3. **Restart application**

4. **Verify:**
   ```bash
   grep "Vector store: pgvector" /var/log/clara.log
   ```

---

## FAQ

**Q: Do I need Redis for the performance improvements?**

A: No. Parallel fetches work without Redis and provide ~60% improvement. Redis adds another ~90% improvement for warm cache scenarios.

**Q: Can I use Redis without migrating to Qdrant?**

A: Yes. Redis caching works with both pgvector and Qdrant.

**Q: What happens if Redis goes down?**

A: The system gracefully degrades to direct API calls. No data is lost.

**Q: How much memory does Redis need?**

A: Typically 50-200MB depending on cache size. Embeddings are ~6KB each.

**Q: Can I run Qdrant and pgvector simultaneously?**

A: Yes, that's what dual-write mode does. Use it during migration.

---

## Support

- **Issues:** https://github.com/anthropics/mypalclara/issues
- **Discussions:** https://github.com/anthropics/mypalclara/discussions

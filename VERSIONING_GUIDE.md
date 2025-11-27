# Version Control Guide - INFERMed

## Current Version Checkpoint

**Tag**: `v1.0.0-stable`  
**Date**: Created before RAG improvements implementation  
**Branch**: `main` (stable), `feature/rag-improvements` (development)

---

## Repository State

### Stable Version (v1.0.0-stable)
This tag marks the stable state of the system with:
- ✅ Complete API integrations (UniProt, KEGG, Reactome, PubChem)
- ✅ Canonical PK/PD dictionary integration
- ✅ Enhanced PK/PD synthesis
- ✅ Multi-level caching (contexts, responses, API calls)
- ✅ Evidence grounding and source attribution
- ✅ Comprehensive documentation
- ✅ Sequential retrieval pipeline
- ✅ Top-K truncation (25 side effects, 10 FAERS, 32 targets, 24 pathways)

### Development Branch (feature/rag-improvements)
This branch is for implementing:
- 🔄 Semantic search with embeddings
- 🔄 Relevance scoring and ranking
- 🔄 Query expansion
- 🔄 Re-ranking with cross-encoders
- 🔄 Hybrid search (keyword + semantic)

---

## How to Rollback

### Option 1: Reset to Stable Tag (Recommended)
If you need to completely revert to the stable version:

```bash
# Switch to main branch
git checkout main

# Reset to the stable tag (HARD RESET - loses uncommitted changes)
git reset --hard v1.0.0-stable

# If you want to keep your changes but switch branches
git stash  # Save current changes
git checkout main
git reset --hard v1.0.0-stable
```

### Option 2: Create a New Branch from Stable Tag
If you want to keep the development work but start fresh:

```bash
# Create a new branch from the stable tag
git checkout -b feature/rag-improvements-v2 v1.0.0-stable

# Your old development branch will still exist
git branch  # See all branches
```

### Option 3: Revert Specific Commits
If you only want to undo specific changes:

```bash
# See commit history
git log --oneline

# Revert a specific commit (creates a new commit that undoes it)
git revert <commit-hash>

# Or reset to a specific commit (removes commits after it)
git reset --hard <commit-hash>
```

### Option 4: Compare and Cherry-Pick
If you want to keep some improvements but revert others:

```bash
# See what changed between stable and current
git diff v1.0.0-stable..HEAD

# Cherry-pick specific commits you want to keep
git checkout main
git cherry-pick <commit-hash>
```

---

## Branch Management

### Current Branches
- `main` - Stable production branch (tagged as v1.0.0-stable)
- `feature/rag-improvements` - Development branch for RAG enhancements

### Switching Between Branches
```bash
# Switch to development branch
git checkout feature/rag-improvements

# Switch back to stable
git checkout main

# See which branch you're on
git branch
```

### Merging Development into Main
Once RAG improvements are tested and stable:

```bash
# Switch to main
git checkout main

# Merge the development branch
git merge feature/rag-improvements

# If there are conflicts, resolve them, then:
git add .
git commit -m "Merge RAG improvements into main"

# Create a new tag for the improved version
git tag -a v1.1.0 -m "Version with RAG improvements: semantic search, relevance scoring, query expansion"
```

---

## Tag Management

### List All Tags
```bash
git tag -l
```

### View Tag Details
```bash
git show v1.0.0-stable
```

### Create a New Tag
```bash
# Annotated tag (recommended)
git tag -a v1.1.0 -m "Description of this version"

# Lightweight tag
git tag v1.1.0

# Tag a specific commit
git tag -a v1.1.0 <commit-hash> -m "Description"
```

### Delete a Tag
```bash
# Delete local tag
git tag -d v1.0.0-stable

# Delete remote tag (if pushed)
git push origin --delete v1.0.0-stable
```

### Push Tags to Remote
```bash
# Push specific tag
git push origin v1.0.0-stable

# Push all tags
git push origin --tags
```

---

## Backup Strategy

### Before Major Changes
1. **Commit all current work**
   ```bash
   git add .
   git commit -m "Checkpoint before [description]"
   ```

2. **Create a tag**
   ```bash
   git tag -a v1.0.0-stable -m "Stable checkpoint"
   ```

3. **Push to remote**
   ```bash
   git push origin main
   git push origin --tags
   ```

### Create a Backup Branch
```bash
# Create a backup branch from current state
git branch backup-before-rag-improvements

# Push backup branch to remote
git push origin backup-before-rag-improvements
```

---

## Recovery Scenarios

### Scenario 1: Development Goes Wrong
```bash
# Discard all changes in development branch
git checkout feature/rag-improvements
git reset --hard v1.0.0-stable

# Or switch back to main
git checkout main
```

### Scenario 2: Need to Compare Current vs Stable
```bash
# See all differences
git diff v1.0.0-stable..HEAD

# See file-by-file changes
git diff v1.0.0-stable..HEAD --stat

# See specific file changes
git diff v1.0.0-stable..HEAD -- path/to/file.py
```

### Scenario 3: Lost Work (Uncommitted)
```bash
# See what would be lost
git status

# Save uncommitted work
git stash save "Work in progress"

# Restore later
git stash pop
```

### Scenario 4: Need to Restore a Deleted File
```bash
# Find when file was deleted
git log --diff-filter=D --summary

# Restore from a specific commit
git checkout <commit-hash>^ -- path/to/file.py
```

---

## Best Practices

1. **Always commit before major changes**
   ```bash
   git add .
   git commit -m "Checkpoint: [description]"
   ```

2. **Create tags for stable versions**
   ```bash
   git tag -a v1.0.0-stable -m "Stable version"
   ```

3. **Use branches for experiments**
   ```bash
   git checkout -b experiment/new-feature
   ```

4. **Push regularly to remote**
   ```bash
   git push origin main
   git push origin --tags
   ```

5. **Keep main branch stable**
   - Only merge tested, working code to main
   - Use feature branches for development

---

## Quick Reference

```bash
# Current state
git status
git log --oneline -5

# Switch to stable
git checkout main
git reset --hard v1.0.0-stable

# Switch to development
git checkout feature/rag-improvements

# Create new checkpoint
git tag -a v1.0.1 -m "New checkpoint"

# See all tags
git tag -l

# Compare versions
git diff v1.0.0-stable..HEAD
```

---

## Next Steps

1. ✅ **Current State**: Tagged as `v1.0.0-stable`
2. ✅ **Development Branch**: `feature/rag-improvements` created
3. 🔄 **Next**: Start implementing RAG improvements in the development branch
4. 📝 **After Testing**: Merge to main and create `v1.1.0` tag

---

**Remember**: You can always return to `v1.0.0-stable` if anything goes wrong!


```markdown
# hermes-agent Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches the core development patterns and conventions used in the `hermes-agent` Python codebase. You'll learn how to structure files, write imports and exports, and follow commit and testing patterns unique to this repository. This guide is ideal for contributors seeking to maintain consistency and quality across the project.

## Coding Conventions

### File Naming
- Use **snake_case** for all file names.
  - Example: `data_processor.py`, `agent_core.py`

### Import Style
- Use **relative imports** within the package.
  - Example:
    ```python
    from .utils import helper_function
    from .models import AgentModel
    ```

### Export Style
- Use **named exports** to explicitly define what is available from a module.
  - Example:
    ```python
    __all__ = ['Agent', 'AgentModel']
    ```

### Commit Patterns
- Commit messages are **freeform** with no strict prefixes.
- Average commit message length is about 35 characters.
  - Example: `Fix agent initialization bug`

## Workflows

### Adding a New Module
**Trigger:** When you need to add new functionality as a separate module.
**Command:** `/add-module`

1. Create a new Python file using snake_case (e.g., `new_feature.py`).
2. Use relative imports to access shared utilities or models.
3. Define `__all__` to specify exported classes/functions.
4. Write corresponding tests in a file matching `*.test.*` pattern.

### Refactoring Imports
**Trigger:** When reorganizing code or moving files.
**Command:** `/refactor-imports`

1. Update all import statements to use relative paths.
    ```python
    from .old_module import OldClass
    ```
2. Ensure all affected files are updated for consistency.
3. Run tests to verify nothing is broken.

### Writing Tests
**Trigger:** When adding or updating features.
**Command:** `/write-test`

1. Create a test file named with the pattern `*.test.*` (e.g., `agent.test.py`).
2. Write test functions for each public function or class.
3. Use the project's preferred (unknown) testing framework.
4. Run all tests to ensure correctness.

## Testing Patterns

- Test files follow the `*.test.*` naming convention.
  - Example: `utils.test.py`, `agent_core.test.py`
- The specific testing framework is not detected; check existing tests for style.
- Each test file should cover the public API of its corresponding module.

## Commands
| Command         | Purpose                                      |
|-----------------|----------------------------------------------|
| /add-module     | Scaffold a new module with conventions       |
| /refactor-imports | Refactor imports to use relative paths      |
| /write-test     | Create a new test file for a module          |
```
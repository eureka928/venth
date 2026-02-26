---
name: New Tool
about: Submit a new tool for the Venth project
---

## Tool Name

<!-- Name of the tool -->

## Summary

<!-- What does this tool do? How does it use the Synth API? -->

## Technical Document

<!-- REQUIRED: The 1-page technical document must be included as README.md inside
     your tool's subfolder (tools/your-tool/README.md). This is a hackathon
     submission requirement — PRs for new tools will not be merged without it.

     The document should cover:
     - What the tool does and the problem it solves
     - Architecture / how it works
     - Synth API integration details
     - Usage instructions
-->

## Related Issues

<!-- Link to related issues: Fixes #123, Closes #456 -->

## Testing

- [ ] Tests pass in mock mode (`python -m pytest tools/<tool-name>/tests/ -v`)
- [ ] Manually tested with mock data
- [ ] Tests added in `tools/<tool-name>/tests/`

## Checklist

- [ ] **1-page technical document at `tools/<tool-name>/README.md`**
- [ ] Tool lives in its own subfolder under `tools/`
- [ ] Tool uses `synth_client.SynthClient` for all Synth API access
- [ ] Code follows project style guidelines
- [ ] Self-review completed

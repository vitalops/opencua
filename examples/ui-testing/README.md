# UI Testing with OpenDesk

## Overview

OpenDesk lets you test web UIs by describing what you want in natural language -- no selectors, no pixel coordinates. It works through the accessibility tree, keyboard, mouse, OCR, and screenshots.

This walkthrough uses **[TaskFlow](https://github.com/vitalops/taskflow-demo)**, a task management app (React + Express) with tabs, modals, forms, tables, toggles, and notifications. It's a good test target because it has the kind of UI variety you'd run into on any real project.

## Quick Start

### 1. Install OpenDesk

```bash
pip install 'opendesk[core,mcp]'
opendesk install
```

Or with JavaScript:

```bash
npm install @vitalops/opendesk-sdk
npx opendesk-js install
```

### 2. Set Up TaskFlow (the Example App)

```bash
git clone https://github.com/vitalops/taskflow-demo.git
cd taskflow-demo
npm run install:all
npm run dev
```

Client at `http://localhost:5173`, API at `http://localhost:3001`. Data is in-memory and resets on restart.

### 3. Open Claude Code and Start Testing

Open Claude Code in any directory. OpenDesk tools are available via MCP -- no additional configuration is needed. Just describe what you want to test.

## What's in TaskFlow

| UI Pattern | Location in TaskFlow |
|---|---|
| Sidebar navigation with active states | All pages (left sidebar) |
| Tabbed interfaces (@headlessui Tabs) | Tasks page (status tabs) |
| Modal dialogs (create, edit, confirm delete) | Tasks page |
| Form inputs (text, textarea, select, date picker) | Create/edit task modals |
| Toggle switches | Settings page |
| Data tables with sorting context | Tasks list page |
| Filter dropdowns | Tasks page toolbar |
| Search inputs | Tasks page toolbar |
| Toast notifications | After CRUD operations |
| Badge/pill components | Priority and status indicators |
| Card grid layouts | Dashboard page |
| Color picker | Projects page (create project modal) |
| Notification dropdown | Top navigation bar |

## Example Test Commands

Natural language prompts you can give Claude with OpenDesk connected.

1. "Take a screenshot of the TaskFlow app and describe the dashboard."
2. "Navigate to the Tasks page and count how many tasks are in progress."
3. "Create a new task called 'Write unit tests' with high priority assigned to Alice."
4. "Delete the task 'Design mockups' and confirm the deletion."
5. "Go to Settings, toggle the dark theme switch, and take a screenshot."
6. "Use OCR to read all team member names from the Team page."
7. "Filter tasks by critical priority and verify the results."
8. "Open the notification bell and read the notifications."
9. "Navigate through every page and take a screenshot of each."
10. "Record a workflow: create a task, change its status to done, then delete it."
11. "Replay the workflow you just recorded."
12. "Check the audit log to see all actions performed."

## Testing Patterns

### Visual Verification

Use `screenshot` to capture the current state of the application, then use `ocr` to extract text from the image. This lets you verify that the correct content is rendered without relying on DOM selectors.

```
Take a screenshot of the Tasks page, then use OCR to read all task titles and confirm "Write unit tests" appears in the list.
```

### Accessibility-First Testing

The `ui` tool inspects the application through its accessibility tree. This means tests interact with elements by role and label rather than pixel coordinates or CSS selectors, making tests more robust and meaningful.

```
Use the ui tool to list all buttons on the current page and verify each one has an accessible label.
```

### Workflow Recording

The `learn` tool records a sequence of actions as a reusable workflow. After recording, `audit` can review what was done. This is useful for building repeatable test scripts from manual exploration.

```
Start recording. Create a new task, set its priority to high, save it, then stop recording. Show me the recorded steps.
```

### Regression Testing

Take screenshots before and after a code change, then compare them. This catches unintended visual regressions in layout, styling, or content.

```
Take a screenshot of the dashboard. Now switch to dark mode. Take another screenshot and describe what changed.
```

## Detailed Test Guide

For a comprehensive test plan with 35+ test scenarios covering every OpenDesk tool, see `TESTING_WITH_OPENDESK.md` in the TaskFlow project root.

## Tool Reference

| Tool | What It Tests | Example |
|---|---|---|
| `screenshot` | Visual state, layout, rendered content | Capture the dashboard after login |
| `ocr` | Text content, labels, data accuracy | Read all task titles from the list view |
| `ui` | Accessibility tree, element roles, labels | Verify all form fields have proper labels |
| `mouse` | Click targets, drag-and-drop, hover states | Click the "Create Task" button |
| `keyboard` | Text input, keyboard shortcuts, tab order | Type a task title and press Enter to save |
| `app` | Application launch and window management | Open the browser to the TaskFlow URL |
| `clipboard` | Copy/paste workflows | Copy a task title and paste it into search |
| `learn` | Workflow recording and replay | Record a full CRUD cycle for tasks |
| `audit` | Action history and verification | Review all actions performed in a test run |
| `schedule` | Timed and deferred actions | Schedule a screenshot every 30 seconds |

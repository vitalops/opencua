# Sample Prompts for UI Testing with OpenDesk

Copy-paste-ready prompts organized by testing category. These assume TaskFlow is running locally. Open Claude Code with OpenDesk connected and paste any prompt below.

---

## Navigation Tests

```
Take a screenshot, then click on "Tasks" in the sidebar navigation. Take another screenshot and confirm the Tasks page loaded by checking for the page heading.
```

```
Navigate through every item in the sidebar: Dashboard, Tasks, Team, and Settings. Take a screenshot of each page and list the page titles.
```

```
Click on the "Tasks" sidebar item and verify it shows an active state. Then click "Dashboard" and verify the active state moves.
```

```
Use the ui tool to list all navigation links in the sidebar and confirm each one has a valid label and role.
```

```
Open the notification dropdown in the top navigation bar, read its contents, then close it by clicking elsewhere on the page.
```

---

## Form Interaction Tests

```
Go to the Tasks page, click "Create Task", and fill in the form: title "Refactor API layer", description "Migrate to new endpoint structure", priority "High", assignee "Alice", and a due date of next Friday. Then click Save.
```

```
Open the create task modal and try to submit it with all fields empty. Take a screenshot and check if validation messages appear.
```

```
Go to Settings and toggle every switch on the page. Take a screenshot after each toggle to confirm the state changed visually.
```

```
Open the create task modal, type "Test task" in the title field, then clear it using keyboard shortcuts (Ctrl+A then Delete). Verify the field is empty.
```

```
Go to Settings, find the color picker, select a new color, and take a screenshot to confirm the selection was applied.
```

---

## CRUD Operation Tests

```
Create a new task titled "Performance audit" with critical priority assigned to Bob. After saving, use OCR on the Tasks page to confirm it appears in the list.
```

```
Find the task called "Design mockups" in the task list. Click edit, change its priority to Low, and save. Verify the priority badge updated.
```

```
Delete the task "Write documentation" by clicking its delete button and confirming the deletion dialog. Take a screenshot to verify it is gone from the list.
```

```
Create three tasks in a row: "Task Alpha", "Task Beta", "Task Gamma". After creating all three, take a screenshot and use OCR to confirm all three appear in the list.
```

```
Find any task in the list, open its detail view, change its status to "Done", save, and verify the status badge updated on the list page.
```

---

## Visual Verification Tests

```
Take a screenshot of the Dashboard page. Use OCR to extract all text and list every metric card title and its value.
```

```
Take a screenshot of the Tasks page. Use OCR to read the table headers and confirm they include Title, Priority, Status, Assignee, and Due Date.
```

```
Go to Settings and enable dark mode. Take a screenshot. Then disable dark mode and take another screenshot. Describe the visual differences between the two.
```

```
Navigate to the Team page. Use OCR to read all team member names and their roles. List them in a table format.
```

```
Filter tasks by "Critical" priority. Take a screenshot and use OCR to verify every visible task has a Critical priority badge. Then clear the filter and confirm all tasks reappear.
```

---

## Workflow Automation Tests

```
Start recording a workflow. Navigate to Tasks, create a new task called "Automated test task" with medium priority, verify it appears in the list, then delete it and confirm deletion. Stop recording and show the recorded steps.
```

```
Replay the last recorded workflow. After replay completes, take a screenshot and check the audit log to verify all steps executed successfully.
```

```
Check the audit log and list every action performed in this session. Group them by tool type (screenshot, mouse, keyboard, etc.) and count how many times each tool was used.
```

# Common Actions and Reusable Test Cases
---

**Common Actions**

---
## 1. What is a common action
 
 Common actions are shared test steps that can be reused across multiple steps.
 These steps will execute in order at the point the common action is called in the test case.
 Example scenarios where common actions are used:

- **1) Logging in:** Often the steps to login and get setup at the start of a test can be reused.
- **2) Searching for a record:** Search menues can have a lot of steps and it is faster to make a common action rather than recreating steps for each test case.
- **3) Repeated workflows:** Ex.Checkout page,Location Information, entire application submissions.

---
 
 ## 2. How to add a common action
 
1. Open **Generator → Test cases** and select or create a test case.
2. In **Steps**, click **+ Common action**.
3. Pick the common action from the dropdown (all actions under `Tests/com/**` are listed).
4. Optional: set **Override** as `screen|field|value` to change one field inside the action.
5. Click **Save**.
---
## 3. What is a reusable test case
 Reusable Test Cases allow one test case to call another test case. The test case will start in its own isolated browser.
 Use a Reusable Test Case when a full test flow should be shared, not just a small action. 
 For Example a license is needed to test a license renewal flow. A reusable tc can be used to create a new license.

---
## 4. How to add a Reusable Test Case
 
1. Open **Generator → Test cases** and select or create a test case.
2. In **Steps**, click **+ Reusable test**.
3. Pick the test case ID from the dropdown (IDs from every system are listed).
4. Click **Save**.
---
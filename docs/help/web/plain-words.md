# Steps in plain words

In the Generator, **Steps > Plain words** lets you write a test the way you would describe it, one step per line:

```
Open the login page
Type "ann" into user name
Enter "ENV.PASSWORD" in password
Click Sign in
Choose "Texas" from state
Tick I agree to the terms
Press Enter
```

**Read the steps** shows what each line became: the page and the field it acts on, and what it does. Only real
fields count: a line is matched against the names of the system's pages and fields and against what their locators
say (label, placeholder, text, role name, the ids in a CSS locator). The page a line acts on is the page of the
line before, unless a line opens another one ("Open the payment page", "On Payment"); a field found only on another
page moves the test there.

| You write | It becomes |
|---|---|
| `Type "x" into <field>`, `Enter "x" in <field>`, `Fill <field> with "x"`, `Set <field> to "x"` | type the value |
| `Choose "x" from <field>`, `Select "x" in <field>` | choose the option |
| `Click <thing>`, `Press <button>`, `Submit <thing>` | click |
| `Tick <box>`, `Check <box>`, `Agree to <thing>` | tick |
| `Press Enter` (or Tab, Escape, ...), `Press Enter in <field>` | press the key (in the field just used, if none is named) |
| `Open <page>`, `Go to <page>`, `On <page>` | the next lines act on that page |

A line that fits several fields equally (**ambiguous**) or none (**unresolved**) is shown with the closest fields;
reword it, or leave it out. With AI assist on, **Ask AI assist** sends only those lines and the catalog of page and
field names; its answer is checked the same way, and a field it names that does not exist is dropped (the line stays
unresolved). Lines AI assist matched are marked **AI**.

**Check on the live page** opens each page as it first loads and says whether each field is found. A page you only
reach by clicking through earlier ones shows its fields as not found; that is the page as loaded, not always a
broken field. **Add to the test** appends the steps (as explicit steps) to the open test case; Save keeps them.

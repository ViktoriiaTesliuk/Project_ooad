# Pocket Ledger

> **How much can I still spend this month?**
> Pocket Ledger is an expense tracker with an AI assistant that answers this question before
> the next purchase. It works in a phone browser.

## 1. Product and goal

### What we are building

Pocket Ledger is a web app for students who manage their own monthly budget. They record
purchases from cash and card in one place. They can type a purchase in plain words or import
a bank statement, and they always see how much of the monthly limit is left.

### The pain it removes

| User pain | How Pocket Ledger solves it |
| --- | --- |
| **Spending is scattered.** Card purchases are in one or more bank apps, cash purchases live in receipts and memory. Nobody adds them up. | Separate **cash** and **card wallets** feed **one monthly overview**. Card transactions arrive from a **CSV statement import**; cash purchases are added by hand or by the assistant. |
| **Logging is tedious, so people quit.** Filling in amount, category, wallet and note after every coffee takes long enough that tracking is abandoned within days. | The user types **"185 UAH for lunch"**. The **AI assistant** drafts the amount, category and description, and the user only reviews and saves. |
| **A list of expenses does not answer the real question.** Banking apps show what was spent, but the student still has to work out what is still affordable. | The **dashboard** subtracts this month's expenses from the user's **monthly limit**. It shows the **remaining budget** and the categories that take the most money. |

### Value proposition

Log a purchase in a few seconds while it is fresh, and see right away how it changes what
you can still spend this month. You see cash and card in one place, nothing is saved without
your confirmation, and the app has no access to your bank account.

## 2. Target users

### Primary segment: who we start with

**First- to third-year students at the Ukrainian Catholic University (UCU) who:**

- live in a dormitory or rented flat, away from their parents;
- have a fixed monthly budget for food, transport and small purchases, from parents, a
  scholarship or a part-time job;
- pay with **both a card and cash**, for example at the canteen, the market or when splitting a bill;
- already try to keep track somewhere (notes app, spreadsheet, checking the bank app balance),
  or regularly run short before the end of the month.

**Typical user.** A second-year student in a dormitory gets money once a month. She pays
for groceries by card and for the canteen and shared taxis in cash. Around the 20th she opens
two bank apps and counts the cash in her wallet to see whether she can afford a weekend trip.

### Why this segment first

- **The pain is sharp.** The budget is small and fixed, so overspending is felt immediately.
- **They are reachable.** We share their course chats, dormitory chats and campus.
- **They are a good test.** If students who already try to track their money do not come back,
  the product does not solve the problem well enough.

### Later segments (not in the pilot)

Young professionals in their first job, teenagers learning to manage pocket money (with
parents), and older adults who want a simple spending record. We will adapt the product to
them only after the student pilot works.

## 3. Promotion and validation

### How we reach the users

| Channel | What we do |
| --- | --- |
| **Course group chats** (Telegram) | Post a 30-second screen recording of "type 185 UAH for lunch → see remaining budget", with a signup link. |
| **Dormitory chats** | The same post, plus an offer to help set it up in person. |
| **Student organisations and clubs** | Ask organisers to share the post; give a 2-minute demo at a meeting. |
| **On campus** | A small poster with a QR code near the canteen and dormitory entrance. |
| **Personal invitations** | Invite students who match the profile and ask each to bring one more. They must not be our close friends. |

**Goal:** 15 pilot participants who match the primary segment. Before onboarding we ask two
screening questions: whether they pay with both cash and card, and whether they have a
monthly budget.

### How we check that they want it

"My friends said it is a good idea" does not count. We only count actions people take with
their **time** or their **money**:

| Signal | What we measure | Target (of 15) |
| --- | --- | --- |
| **Sign-up** | Created an account | 12 |
| **Activation**: they tried it for real | Set a monthly limit **and** saved a first expense from their own budget | 8 |
| **Retention**: they gave it their time | An activated user came back **in the following week without a personal reminder** and saved another expense | 5 |
| **Payment**: the strongest signal | After one week, the app shows a **Support** button that leads to a donation to a volunteer fund; we count donors and the total amount | 3 donors |

Retention is our main evidence. A user who comes back on their own is spending their own time
on the product. A donation goes one step further: it shows the product was worth money to them.

### Product-specific checks

- **AI usefulness.** Each participant records five typical purchases twice, once by hand and
  once with the assistant, and we time both. We also log how often users **keep the
  suggested category** when they save a draft. Target: the assistant is faster for most
  participants and its category is kept in at least 70% of drafts.
- **Usability.** We ask each participant to add an expense, correct its amount and find the
  remaining budget, without help. We write down every place where they hesitate or ask a
  question.
- **Interviews.** We hold five-minute conversations with three users who came back and three
  who did not, to learn what made the difference.

### Data we collect

The app records sign-ups, the first expense, return visits by week, AI drafts (accepted or
changed category) and CSV imports. An in-app **Feedback** button collects comments. Donations
are confirmed with screenshots from the fundraiser.

### What we do if a target is missed

| If... | Then... |
| --- | --- |
| Fewer than 8 activate | Shorten onboarding: create a default wallet and suggest a limit automatically. |
| Activated users do not return | Interview them; test a weekly "budget left" summary as a reminder. |
| Users often change the AI category | Improve the prompt with their real examples; show the top 3 categories instead of one. |
| Nobody donates | Ask in the interviews what the app would need to be worth paying for. |

We will report **all** the numbers we observe, including the targets we missed.

## 4. Features: MVP (first version)

| Feature | What it does |
| --- | --- |
| **Account** | Sign in with Google; every user sees only their own data. |
| **Wallets** | Separate cash and card wallets. Other currencies are converted at Monobank rates (NBU as fallback) for the totals. |
| **Transactions** | Add, edit and delete income and expenses; confirmation before deleting. |
| **History** | Filter transactions by month, wallet or category. |
| **AI assistant** | Turns a short description ("185 UAH for lunch") into an editable draft with amount, category and note. Nothing is saved until the user confirms. |
| **CSV import** | Upload a bank statement into a chosen wallet, check a preview with validation, then confirm. |
| **Monthly limit and dashboard** | Total spent, **remaining budget** and spending by category for the current month. |
| **Feedback and Support** | In-app feedback form and a link to a volunteer fundraiser, used for the pilot. |
| **Mobile-friendly layout** | Works in a phone browser with clear labels and readable totals. |

**Not in the first version:** connecting to bank accounts, shared budgets, savings goals,
notifications and native mobile apps.

## 5. Product pipeline

### What the user does

```
1. Set up     →   2. Capture         →   3. Review and save    →   4. Decide
Sign in with      Type "185 UAH for      Check the draft or the     See the remaining
Google, create    lunch", enter it by    CSV preview; fix the       budget and the top
a wallet, set a   hand, or upload a      wallet, date and amount;   categories, then
monthly limit     CSV statement          confirm                    decide on the next purchase
```

### What happens inside

1. **Set up.** After Google sign-in the app verifies the user's identity and creates their
   account and default categories. The user creates a wallet and sets a monthly spending limit.
2. **Capture.**
   - *AI draft:* the Python app sends the user's text and their list of categories to the
     Gemini API and asks for a structured answer (amount, currency, category, note).
   - *CSV import:* the app parses the uploaded file and checks every row's date and amount.
   - *Manual entry:* the ordinary form. It is always available, including when the assistant
     is unavailable.
3. **Review and save.** The app validates the AI answer: the amount must be positive and the
   category must be one of the user's categories. It then fills the form. The user confirms the
   wallet, date and values. Only then does the app save the transaction to PostgreSQL and update
   the wallet balance. For a CSV file, the user confirms the preview and the rows are saved
   together. Rows that were already imported are skipped.
4. **Decide.** The dashboard recalculates this month's spending and the remaining budget from
   the saved transactions. Amounts in other currencies are converted at current rates.

### What we learn from it

Every step of the pipeline produces a validation signal: sign-up and limit at **set-up**, AI
drafts and imports at **capture**, kept or changed categories at **review**, and return visits
at **decide**. The pilot report is built from these numbers.

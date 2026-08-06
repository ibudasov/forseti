# Coding Agent Clean Code Instructions

# Coding Agent Instruction: Clean Code + Object Calisthenics

When writing, editing, reviewing, or refactoring code, follow these rules by default. The goal is to produce code that is **readable, simple, maintainable, testable, and easy for another developer to change**.

Use these principles as practical engineering constraints, not as dogma. If a rule must be violated, the code should make the reason obvious, or the trade-off should be explicitly explained.

## Core objective

Write code that can be understood and safely changed by someone other than the original author.

Clean code should be:

- easy to read
- easy to change
- easy to test
- easy to extend
- hard to misuse
- explicit about intent
- free of unnecessary complexity

Prefer simple, boring, obvious code over clever code.

---

## General clean code rules

### 1. Follow existing project conventions

Before adding new code:

- inspect nearby files
- follow the existing architecture
- follow naming, formatting, and testing conventions
- reuse existing abstractions where appropriate

Do not introduce a new pattern, framework, naming style, or folder structure unless it clearly improves the design.

### 2. Keep it simple

Prefer the simplest solution that satisfies the requirement.

Avoid:

- speculative abstractions
- premature generalization
- clever one-liners
- unnecessary inheritance
- hidden magic
- overly generic helpers
- large “utility” modules

If the code is hard to explain, simplify it.

### 3. Apply the Boy Scout Rule

Leave the code cleaner than you found it.

When touching code, improve nearby problems if the improvement is safe and scoped:

- rename unclear variables
- extract confusing logic
- remove duplication
- simplify conditionals
- add missing tests
- delete dead code

Do not perform broad unrelated refactors unless explicitly asked.

### 4. Always look for the root cause

When fixing bugs, do not only patch symptoms.

Before changing code:

- identify why the bug happened
- check whether similar bugs exist nearby
- add a regression test when possible
- fix the design if the design caused the bug

---

## Naming rules

### 5. Use meaningful names

Names must reveal intent.

Prefer:

```
calculatePositionSize
validatedTicker
riskRewardRatio
earningsDate
```

Avoid:

```
calc
data
tmp
val
x
obj
```

A good name should reduce the need for comments.

### 6. Do not abbreviate

Avoid abbreviations unless they are universally understood in the project domain.

Prefer:

```
customer
positionSize
recommendation
technicalIndicator
```

Avoid:

```
cust
posSz
rec
techInd
```

If a name becomes too long, it is often a sign that the function or class has too many responsibilities.

### 7. Use pronounceable and searchable names

Code is read, discussed, searched, and reviewed by humans.

Avoid names that are hard to say or search for:

```
dtaRcrd
trdCfg
fndScr
```

Prefer complete domain names:

```
tradeConfiguration
fundamentalScore
marketDataRecord
```

---

## Function and method rules

### 8. Functions should do one thing

Each function should have one clear responsibility.

If a function:

- validates input
- fetches data
- transforms data
- applies business rules
- logs output
- writes to storage

all in one place, split it.

A function should be describable with a short sentence.

### 9. Keep functions small

Prefer small functions with clear names.

As a guideline:

- ideal: 5–15 lines
- acceptable: longer only when it remains linear and obvious
- suspicious: functions that require scrolling
- bad: functions with multiple sections, nested branches, and mixed abstraction levels

Extract methods when logic becomes hard to scan.

### 10. Use one level of abstraction per function

Do not mix high-level business steps with low-level implementation details.

Bad:

```python
def analyze_ticker(ticker):
    response = requests.get(...)
    rows = response.json()["prices"]
    rsi = calculate_rsi(rows)
    if rsi < 30:
        ...
```

Better:

```python
def analyze_ticker(ticker):
    price_history = market_data_provider.load_price_history(ticker)
    technical_signal = technical_analyzer.analyze(price_history)
    return recommendation_engine.recommend(technical_signal)
```

Low-level details belong in lower-level functions or collaborators.

### 11. Prefer few arguments

Avoid long parameter lists.

If a function needs many parameters, introduce a meaningful object:

```python
@dataclass(frozen=True)
class RiskConfig:
    account_size: Money
    risk_per_trade: Percentage
    maximum_position_size: Money
```

Long argument lists often indicate missing domain concepts.

### 12. Avoid flag arguments

A boolean flag often means the function does two things.

Avoid:

```python
generate_report(data, include_debug=True)
```

Prefer separate functions:

```python
generate_report(data)
generate_debug_report(data)
```

### 13. Avoid side effects where possible

A function should not unexpectedly mutate state, write to disk, call external services, or change global configuration.

If side effects are necessary:

- make them explicit in the function name
- isolate them at system boundaries
- keep pure business logic separate from I/O

Prefer:

```python
save_recommendation(recommendation)
```

over hidden writes inside:

```python
calculate_recommendation(...)
```

---

## Object Calisthenics rules

Use the following Object Calisthenics rules as strong design guidance.

### 14. Only one level of indentation per method

Avoid deeply nested code.

Bad:

```python
def process(order):
    if order.is_valid():
        if order.has_stock():
            if order.is_paid():
                ship(order)
```

Better:

```python
def process(order):
    if not order.is_valid():
        return

    if not order.has_stock():
        return

    if not order.is_paid():
        return

    ship(order)
```

If a method has nested loops or nested conditionals, extract methods or move behavior to the right object.

### 15. Do not use `else` when early return is clearer

Prefer guard clauses and early returns.

Bad:

```python
if valid:
    process()
else:
    reject()
```

Better:

```python
if not valid:
    reject()
    return

process()
```

For complex branching, consider:

- polymorphism
- strategy pattern
- state pattern
- lookup tables
- explicit rule objects

Do not replace a simple conditional with unnecessary abstraction.

### 16. Extract complex conditions into named functions or variables

**Code Calisthenics for Conditionals**: Complex boolean expressions should be extracted into well-named functions or variables to improve readability and intent.

**The Problem**: Conditional logic embedded in if statements is hard to read and understand:

```python
if user.age >= 18 and user.has_valid_license and user.driving_record_clean and not user.is_suspended:
    grant_access()
```

**Named Variables** (for simple cases):

```python
is_eligible_driver = (
    user.age >= 18 and 
    user.has_valid_license and 
    user.driving_record_clean and 
    not user.is_suspended
)

if is_eligible_driver:
    grant_access()
```

**Named Methods** (preferred approach):

```python
if user.is_eligible_to_drive():
    grant_access()

# Implementation:
class User:
    def is_eligible_to_drive(self) -> bool:
        return (
            self.age >= 18 and 
            self.has_valid_license and 
            self.driving_record_clean and 
            not self.is_suspended
        )
```

**Benefits**:
- Improves readability with self-documenting intent
- Enables reuse of the condition logic
- Simplifies maintenance (change logic in one place)
- Easier to test the condition independently
- Named methods make the reason for the check explicit

**Best Practices**:
- Use positive language: `is_valid()` rather than `is_not_invalid()`
- Each condition method checks one responsibility
- Return boolean values
- Group logically related checks together
- Extract early before conditions become unmaintainable

### 17. Wrap primitives and strings when they carry domain meaning

Avoid “primitive obsession”.

If a primitive has validation rules, formatting rules, units, or domain meaning, wrap it in a value object.

Examples:

```
Ticker
Money
Percentage
RiskRewardRatio
StopLoss
TakeProfit
PositionSize
EmailAddress
DateRange
```

Bad:

```python
def calculate_position_size(account_size: float, risk: float, stop_loss: float):
    ...
```

Better:

```python
def calculate_position_size(
    account_size: Money,
    risk_per_trade: Percentage,
    stop_loss_distance: PriceDistance,
) -> PositionSize:
    ...
```

This makes illegal states harder to represent.

### 18. Use first-class collections

Do not spread collection manipulation across unrelated code.

If a collection has domain behavior, wrap it.

Bad:

```python
signals = []
signals.append(signal)
valid_signals = [s for s in signals if s.is_valid()]
```

Better:

```python
class TechnicalSignals:
    def __init__(self, signals):
        self._signals = signals

    def valid(self):
        return TechnicalSignals([
            signal for signal in self._signals
            if signal.is_valid()
        ])
```

Collection wrappers are useful for:

- filtering
- sorting
- validation
- aggregation
- enforcing invariants
- preventing external mutation

### 19. Use one dot per line as a warning signal

Avoid long chains that expose object internals.

Bad:

```python
city = order.customer.address.city.upper()
```

Better:

```python
city = order.customer_city()
normalized_city = city.upper()
```

Or better still:

```python
normalized_city = order.normalized_customer_city()
```

Long chains often violate encapsulation and the Law of Demeter.

Exceptions are acceptable for fluent builders, query builders, test assertions, or framework APIs when the chain is idiomatic and readable.

### 20. Keep entities small

Classes, modules, functions, and packages should stay small and cohesive.

Watch for:

- large files
- large classes
- “manager” classes
- “service” classes with many unrelated methods
- modules that mix business logic, I/O, validation, and formatting

A class should have one reason to change.

If a class name contains words like `Manager`, `Helper`, `Util`, or `Processor`, check whether the responsibility is too vague.

### 21. Prefer high cohesion

A class should contain data and behavior that strongly belong together.

If a class has fields that are only used by some methods but not others, split it.

If a method mostly works with another object’s data, move the method closer to that data.

### 22. Limit instance variables

As a design exercise, aim for classes with no more than two instance variables where practical.

This is not always mandatory, but more fields usually mean lower cohesion.

When a class needs many fields, look for hidden concepts that deserve their own object.

Example:

```python
class TradeRecommendation:
    decision: Decision
    risk_plan: RiskPlan
    thesis: TradeThesis
```

Instead of:

```python
class TradeRecommendation:
    ticker: str
    decision: str
    entry_min: float
    entry_max: float
    stop_loss: float
    take_profit: float
    confidence: float
    reasons: list[str]
    warnings: list[str]
```

### 23. Avoid getters and setters as the default design

Do not expose internal state just so other code can make decisions.

Prefer “tell, don’t ask”.

Bad:

```python
if account.get_balance() >= amount:
    account.set_balance(account.get_balance() - amount)
```

Better:

```python
account.withdraw(amount)
```

Objects should protect their own invariants.

Getters are acceptable for:

- DTOs
- serialization
- read models
- API responses
- UI display
- ORM boundaries

But business decisions should usually live inside domain objects.

---

## Comments and documentation

### 24. Prefer self-documenting code

Do not use comments to explain confusing code. First, make the code clearer.

Bad:

```python
# Check if trade is risky
if x > 0.08 and y < 1.5:
    ...
```

Better:

```python
if risk_policy.rejects_trade(drawdown, risk_reward_ratio):
    ...
```

### 25. Use comments for why, not what

Good comments explain:

- non-obvious business rules
- trade-offs
- external constraints
- performance decisions
- security considerations
- temporary workarounds with expiry or issue links

Avoid comments that repeat the code.

### 26. Keep comments accurate

Outdated comments are worse than no comments.

When changing code, update or delete affected comments.

---

## Error handling rules

### 27. Fail fast

Validate inputs at boundaries.

Reject invalid states early.

Prefer explicit errors over silent failure.

### 28. Use meaningful exceptions or error results

Errors should explain:

- what failed
- why it failed
- what input or state caused the failure
- whether the caller can recover

Avoid generic errors like:

```
Error
Something went wrong
Invalid input
```

### 29. Do not swallow errors

Avoid empty catches.

Bad:

```python
try:
    sync_data()
except Exception:
    pass
```

If ignoring an error is intentional, explain why and log enough context.

---

## Testing rules

### 30. Write tests for behavior, not implementation details

Tests should describe what the system does from the caller’s perspective.

Avoid tests that break after harmless refactoring.

### 31. Keep tests clean too

Test code must follow the same readability standards as production code.

Use:

- clear test names
- arrange / act / assert structure
- meaningful fixtures
- small test cases
- explicit expected values

Avoid excessive mocking unless needed.

### 32. Add regression tests for bugs

When fixing a bug:

1. write or update a failing test that reproduces the bug
2. fix the bug
3. verify the test passes

---

## Design and architecture rules

### 33. Separate business logic from infrastructure

Keep domain logic independent from:

- databases
- HTTP clients
- queues
- cloud SDKs
- frameworks
- UI code
- CLI code

Business logic should be testable without real infrastructure.

### 34. Separate UI/API code from business rules

Controllers, routes, handlers, and UI components should be thin.

They should:

- parse input
- call application/domain services
- map results to responses

They should not contain core business decisions.

### 35. Prefer composition over inheritance

Use inheritance only when there is a true substitutable “is-a” relationship.

Prefer:

- small objects
- interfaces/protocols
- dependency injection
- strategies
- composition

Avoid deep inheritance hierarchies.

### 36. Avoid duplication

Remove duplication in:

- code
- business rules
- validation
- constants
- data transformations
- test setup

But do not create a poor abstraction just to remove two similar lines.

Duplication is better than the wrong abstraction.

### 37. Make dependencies explicit

Avoid hidden global dependencies.

Prefer passing dependencies through constructors or function parameters.

This improves testability and makes code easier to reason about.

---

## Code review checklist for agents

Before returning code, verify:

- [ ]  The code follows existing project conventions.
- [ ]  Names are clear, complete, and not abbreviated.
- [ ]  Functions are small and do one thing.
- [ ]  Each function has one level of abstraction.
- [ ]  There is no unnecessary nesting.
- [ ]  `else` is avoided where guard clauses are clearer.
- [ ]  Complex conditionals are extracted into named functions or variables.
- [ ]  Domain primitives are wrapped when they carry business meaning.
- [ ]  Collections with behavior are first-class objects.
- [ ]  Long method chains are avoided unless idiomatic.
- [ ]  Classes and modules are small and cohesive.
- [ ]  Business logic is separated from I/O, framework, and UI code.
- [ ]  Objects protect their own invariants.
- [ ]  There are no unnecessary getters/setters.
- [ ]  Errors are explicit and not swallowed.
- [ ]  Tests cover meaningful behavior.
- [ ]  The code is simpler after the change than before.
- [ ]  Any rule violation is intentional and justified.

---

## Refactoring strategy

When improving existing messy code:

1. Characterize current behavior with tests first.
2. Rename unclear concepts.
3. Extract small functions.
4. Replace nesting with guard clauses.
5. Move behavior to the object that owns the data.
6. Introduce value objects for domain primitives.
7. Wrap collections that contain business behavior.
8. Separate business rules from infrastructure.
9. Delete dead code.
10. Run tests and linters after each meaningful step.

Prefer many safe, small refactors over one large rewrite.

---

## Important trade-off rule

Do not apply Clean Code or Object Calisthenics mechanically.

These rules are meant to improve:

- readability
- maintainability
- testability
- domain clarity
- encapsulation

If applying a rule makes the code harder to understand, more fragmented, or unnecessarily abstract, choose the clearer design and explain the trade-off.

The final code should be clean because it is **easy to understand and change**, not because it blindly satisfies a checklist.

## Reference sources

- [Summary of Clean Code by Robert C. Martin](https://gist.github.com/wojteklu/73c6914cc446146b8b533c0988cf8d29)
- [Understanding Object Calisthenics: Writing Cleaner Code](https://dev.to/muzammilnm/understanding-object-calisthenics-writing-cleaner-code-maj)
- [Improve Your Code With Object Calisthenics](https://www.softensity.com/blog/improve-your-code-with-object-calisthenics/)
- [Object Calisthenics — Developer Handbook](https://developerhandbook.stakater.com/architecture/object-calisthenics.html)
- [Object Calisthenics — William Durand](https://williamdurand.fr/2013/06/03/object-calisthenics/)
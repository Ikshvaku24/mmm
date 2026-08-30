# How 1.645 came into HDI

We multiply by $1.645$ because standard deviation ($\sigma$) only measures the **width of a single standard unit**, whereas an interval (like a 90% HDI) measures the **distance from the center to a specific boundary**.

To get the actual distance on the graph, you have to multiply the *unit size* ($\sigma$) by the *number of units needed to reach the boundary* ($1.645$).

---

### 1. The Analogy: Measuring Distance in Steps

Think of standard deviation ($\sigma$) as the **length of your stride**, and the interval as the **distance to a fence**.

* $\sigma = 0.20$ means your stride length is **$0.20$ log-units**.
* The 90% boundary sits **$1.645$ strides away** from the center.

To find out how many log-units far away the fence is:

$$\text{Distance to fence} = (\text{Length of 1 stride}) \times (\text{Number of strides})$$

$$\text{Log Margin} = \sigma \times 1.645 = 0.20 \times 1.645 = \mathbf{0.329}$$

---

### 2. The Math Behind the Multiplication

Any normal distribution $X \sim \text{Normal}(\mu, \sigma)$ can be converted to a standard z-score using:

$$z = \frac{X - \mu}{\sigma}$$

We want to find the boundary value $X_{\text{high}}$ where the upper tail cut-off leaves $5\%$ of the probability above it ($z = 1.645$):

$$1.645 = \frac{X_{\text{high}} - \mu}{\sigma}$$

Now, rearrange the equation using algebra to isolate $X_{\text{high}}$:

1. Multiply both sides by $\sigma$:

$$1.645 \times \sigma = X_{\text{high}} - \mu$$


2. Add $\mu$ to both sides:

$$X_{\text{high}} = \mu + (1.645 \times \sigma)$$



The term $(1.645 \times \sigma)$ is the **Log Margin**—the exact quantity you must add to the center $\mu$ to reach the 90% interval's upper edge.

---

### 3. Summary

| Term | What it is | Example Value |
| --- | --- | --- |
| **$\sigma$** | The size of **1** standard deviation. | $0.20$ log-units |
| **$1.645$** | The **number of standard deviations** required to cover 90% of the distribution. | $1.645$ standard deviations |
| **$1.645 \times \sigma$** | The **total distance (margin)** from the center to the 90% boundary. | $0.329$ log-units |

Once you have that total log-distance ($0.329$), you add it to the mean ($\mu + 0.329$) and exponentiate to get the real-world upper bound ($\text{hdi\_high}$).

<hr style="border: 2px solid green;">

# If i want 20% uncertainity in mean for a feature in the feature scale itself then i would put 20% in the formula?

Yes, exactly! That is **100% correct**.

When you calculate `prior_sd` using that exact formula:

$$\text{prior\_sd} = \sqrt{\log\left(1 + \left(\frac{s}{m}\right)^2\right)}$$

where $\frac{s}{m} = 0.20$ ($20\%$ uncertainty), you get:

$$\text{prior\_sd} = \sqrt{\log(1 + 0.20^2)} = \sqrt{\log(1.04)} \approx \mathbf{0.198}$$

---

### What happens when that $0.198$ goes into your model

Here is how that input flows all the way through to the final outputs discussed in step 5:

#### 1. In the `__population__` output row:

The model takes $\text{pop\_sd} = 0.198$. To find the 90% HDI upper bound (`hdi_high`), it calculates:

$$\text{Log Margin} = 1.645 \times 0.198 = \mathbf{0.325}$$

$$\text{hdi\_high} = \text{Mean} \times \exp(+0.325) \approx \text{Mean} \times 1.384 \quad (+\mathbf{38.4\%})$$

Notice that $+38.4\%$ is roughly **double** your 1-standard-deviation relative uncertainty ($20\%$), which makes total sense because a 90% interval extends out to $\approx 1.645 \sigma$ in both directions!

---

#### 2. In the Region output rows (e.g., `1-Walmart`):

The model combines your `pop_sd` ($0.198$) with the regional heterogeneity variance ($\tau^2$):

$$\text{Combined Log SD} = \sqrt{0.198^2 + \tau^2}$$

If regional scatter $\tau = 0.40$, the combined spread becomes $\sqrt{0.198^2 + 0.40^2} \approx 0.447$.

Then `hdi_high` for Walmart becomes:

$$\text{Log Margin} = 1.645 \times 0.447 = \mathbf{0.735}$$

$$\text{hdi\_high} = \text{Mean} \times \exp(+0.735) \approx \text{Mean} \times 2.08 \quad (+\mathbf{108\%})$$

---

### Key takeaway for your CSV setup

By putting $\sqrt{\log(1 + (s/m)^2)}$ into your prior CSV file, you are feeding the model the exact mathematically correct log-space standard deviation.

* The **`__population__`** row's `hdi_high` will cleanly match your intended $20\%$ base uncertainty (scaled up to the 90% HDI level).
* Any extra width in individual **Region rows** is purely the additional regional variation ($\tau$) that the data or `regional_sd` introduced.

<hr style="border: 2px solid green;">

# what is s/m here?

In that equation, $\frac{s}{m}$ is the **Coefficient of Variation (CV)**—the relative percentage uncertainty of your parameter in natural (unscaled) units.

### Component Breakdown

* **$s$ (Standard Deviation in natural units):** The absolute dollar/unit margin of uncertainty you expect around your prior mean (e.g., $s = \$20$).
* **$m$ (Mean in natural units):** The expected center point of your parameter in natural units (e.g., $m = \$100$).
* **$\frac{s}{m}$ (Relative Uncertainty Ratio):** Expresses that standard deviation as a percentage fraction of the mean.

$$\frac{s}{m} = \frac{\$20}{\$100} = 0.20 \quad (20\%)$$

---

### Why it takes this specific form

When you plug $0.20$ into $\sqrt{\log\left(1 + \left(\frac{s}{m}\right)^2\right)}$, you are converting a **linear percentage spread** ($\frac{s}{m} = 0.20$) into the exact **log-scale standard deviation** ($\sigma = 0.198$) that PyMC/Stan requires.

It ensures that when the log-normal model exponentiates its parameters back to natural space, 1 standard deviation of uncertainty maps to $20\%$ of your target mean ($m$).

<hr style="border: 2px solid green;">

# Derive this formula
# we are using median instead of mean thus no subtraction of (sigma^2/2)

Absolutely. The core idea is actually quite simple:

> **You want to describe your prior in terms humans understand — e.g. "β is around 100, with about 20% relative uncertainty" — but a LogNormal distribution is parameterized using the mean and SD of `log(β)`.**

The formula is just the conversion between those two descriptions.

---

## 1. First understand what is happening

Suppose you want a prior for a positive parameter `β`.

You might tell the model:

> "I expect β to be around 100, and its standard deviation should be 20."

So in the **real-world scale**:

$$
m=100
$$

$$
s=20
$$

Therefore:

$$
\frac{s}{m}=\frac{20}{100}=0.20
$$

So you are saying:

> **β has 20% relative uncertainty.**

But suppose your model uses:

$$
\log(\beta)\sim Normal(\mu,\sigma^2)
$$

PyMC/Stan needs the parameters:

$$
\mu,\sigma
$$

You can't simply say:

$$
\sigma=0.20
$$

because **0.20 is a relative SD on the natural scale**, whereas `σ` is the SD on the **log scale**.

That's the entire reason for the formula.

---

## 2. Why are we using the log scale?

A LogNormal distribution is created by taking a Normal distribution and exponentiating it.

Start with:

$$
X\sim Normal(\mu,\sigma^2)
$$

Then define:

$$
\beta=e^X
$$

Therefore:

$$
\log(\beta)=X
$$

and hence:

$$
\log(\beta)\sim Normal(\mu,\sigma^2)
$$

So conceptually:

```text
Normal distribution
       ↓
log(β)
       ↓
   exponentiate
       ↓
     β
```

The Normal distribution lives on the **log scale**, while your actual parameter `β` lives on the **natural scale**.

---

## 3. Why can't we just use σ = 20?

Suppose:

$$
\beta\sim LogNormal(\mu,20^2)
$$

That means:

$$
SD(\log\beta)=20
$$

It does **NOT** mean:

$$
SD(\beta)=20
$$

Those are completely different things.

For example:

```text
Natural scale:

β = 100 ± 20

means roughly:
80 → 120
```

But:

```text
Log scale:

log(β) = μ ± 20
```

would correspond to an astronomically huge range after exponentiating.

So we have to convert.

---

## 4. Start from what you actually care about

Suppose you know:

$$
m=100
$$

and:

$$
s=20
$$

You want your LogNormal distribution to have:

$$
E[\beta]=100
$$

and:

$$
SD(\beta)=20
$$

The problem is:

> What values of `μ` and `σ` produce those natural-scale properties?

---

## 5. The LogNormal mean formula

For:

$$
\log(\beta)\sim Normal(\mu,\sigma^2)
$$

the natural-scale mean is:

$$
m=e^{\mu+\frac{\sigma^2}{2}}
$$

This is where the first "surprise" occurs.

You might expect:

$$
m=e^\mu
$$

but that's **not correct**.

Why?

Because exponentiation is nonlinear.

The average of exponentials is not the exponential of the average:

$$
E[e^X]\neq e^{E[X]}
$$

Instead:

$$
E[e^X]=e^{\mu+\sigma^2/2}
$$

That extra:

$$
\frac{\sigma^2}{2}
$$

is caused by the curvature of the exponential function.

---

## 6. The variance formula

For a LogNormal:

$$
Var(\beta)
=
(e^{\sigma^2}-1)e^{2\mu+\sigma^2}
$$

At first this looks ugly.

But notice something useful.

We already know:

$$
m=e^{\mu+\sigma^2/2}
$$

Square it:

$$
m^2=e^{2\mu+\sigma^2}
$$

So we can replace:

$$
e^{2\mu+\sigma^2}
$$

with:

$$
m^2
$$

giving:

$$
Var(\beta)
=
(e^{\sigma^2}-1)m^2
$$

Much simpler.

---

## 7. Now introduce the standard deviation

You said:

$$
SD(\beta)=s
$$

Therefore:

$$
Var(\beta)=s^2
$$

So:

$$
s^2=(e^{\sigma^2}-1)m^2
$$

Now divide by \(m^2\):

$$
\frac{s^2}{m^2}=e^{\sigma^2}-1
$$

And:

$$
\frac{s^2}{m^2}
=
\left(\frac{s}{m}\right)^2
$$

Therefore:

$$
\left(\frac{s}{m}\right)^2=e^{\sigma^2}-1
$$

Now we're almost there.

---

## 8. Solve for σ

Add 1:

$$
1+\left(\frac{s}{m}\right)^2=e^{\sigma^2}
$$

Take log:

$$
\log\left(1+\left(\frac{s}{m}\right)^2\right)=\sigma^2
$$

Take square root:

$$
\boxed{
\sigma=
\sqrt{
\log\left(
1+\left(\frac{s}{m}\right)^2
\right)
}
}
$$

That's the formula.

---

## 9. Let's actually calculate it

Suppose:

$$
m=100
$$

and:

$$
s=20
$$

Therefore:

$$
\frac{s}{m}=0.20
$$

Plug it in:

$$
\sigma=
\sqrt{\log(1+0.20^2)}
$$

First:

$$
0.20^2=0.04
$$

Then:

$$
1+0.04=1.04
$$

Then:

$$
\log(1.04)\approx0.03922
$$

Then:

$$
\sigma=\sqrt{0.03922}
$$

So:

$$
\boxed{\sigma\approx0.198}
$$

Notice something interesting:

You said **20% relative uncertainty**, and the log-scale SD comes out to approximately **0.198**, which is very close to 0.20.

That's why people sometimes mistakenly use 0.20 directly.

---

## 10. But the difference becomes bigger with larger uncertainty

Suppose instead:

$$
\frac{s}{m}=0.50
$$

That's 50% relative SD.

Using the formula:

$$
\sigma=
\sqrt{\log(1+0.5^2)}
$$

$$
=\sqrt{\log(1.25)}
$$

$$
\approx0.472
$$

So:

```text
Natural-scale relative SD     Log-scale σ

20%                           0.198
50%                           0.472
100%                          0.833
200%                          1.269
```

The conversion becomes increasingly important as uncertainty gets larger.

---

## 11. There's another formula you need: μ

This is also important.

Your quoted explanation focuses on converting the **SD**, but if you want to completely translate your natural-scale prior into a LogNormal prior, you also need to convert the mean.

You have:

$$
m=e^{\mu+\sigma^2/2}
$$

Take log:

$$
\log(m)=\mu+\frac{\sigma^2}{2}
$$

Therefore:

$$
\boxed{
\mu=\log(m)-\frac{\sigma^2}{2}
}
$$

So the complete conversion is:

$$
\boxed{
\sigma=
\sqrt{
\log\left(1+\left(\frac{s}{m}\right)^2\right)
}
}
$$

and

$$
\boxed{
\mu=\log(m)-\frac{\sigma^2}{2}
}
$$

---

## 12. Complete example

Suppose your business knowledge says:

> "The coefficient β should be around 100, with 20% relative uncertainty."

Therefore:

$$
m=100
$$

$$
s=20
$$

### Step 1 — Calculate relative SD

$$
CV=\frac{s}{m}=0.20
$$

### Step 2 — Calculate log-scale σ

$$
\sigma=
\sqrt{\log(1+0.20^2)}
$$

$$
\sigma\approx0.198
$$

### Step 3 — Calculate log-scale μ

$$
\mu=\log(100)-\frac{0.198^2}{2}
$$

$$
\mu\approx4.585
$$

So your PyMC prior could conceptually be:

```text
log(β) ~ Normal(μ = 4.585, σ = 0.198)
```

or equivalently:

```text
β ~ LogNormal(μ = 4.585, σ = 0.198)
```

And importantly, this produces approximately:

```text
Natural scale:

Mean ≈ 100
SD   ≈ 20
```

---

## 13. Why is μ slightly less than log(100)?

This is another subtle point.

You might initially think:

$$
\mu=\log(100)=4.605
$$

But that's not quite right.

Remember:

$$
E[\beta]=e^{\mu+\sigma^2/2}
$$

Because of the extra:

$$
\frac{\sigma^2}{2}
$$

if you used:

$$
\mu=\log(100)
$$

your natural-scale mean would actually be **greater than 100**.

Therefore we subtract:

$$
\frac{\sigma^2}{2}
$$

to make sure the **actual natural-scale mean** is 100.

---

## 14. The intuition behind the whole transformation

Think of it as two languages.

### Human/business language

You say:

> "β should be around 100, and I expect roughly 20% uncertainty."

That's:

$$
m=100
$$

$$
s/m=20\%
$$

### Model's language

PyMC/Stan needs:

$$
\log(\beta)\sim Normal(\mu,\sigma^2)
$$

So we translate:

```text
Human specification
       │
       │  m = 100
       │  s/m = 20%
       ↓
Conversion formulas
       │
       ↓
μ = 4.585
σ = 0.198
       │
       ↓
LogNormal prior
       │
       ↓
β on natural scale
       │
       ↓
Mean ≈ 100
SD ≈ 20
```

### The most important takeaway

The formula

$$
\boxed{
\sigma=
\sqrt{
\log\left(1+\left(\frac{s}{m}\right)^2\right)
}
}
$$

is **not some arbitrary correction factor**.

It comes directly from the mathematical relationship between the **natural-scale variance of a LogNormal distribution** and the **standard deviation of its underlying Normal distribution**.

And one terminology point: **Stan doesn't inherently "operate on the log scale" for every parameter.** This applies when *you choose a LogNormal parameterization* (or another transformed parameterization). PyMC and Stan can work with parameters on many scales; here, the log scale is a consequence of choosing a LogNormal prior.
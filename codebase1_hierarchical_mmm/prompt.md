this converstation will be limited to codebase1 and check codebase1_hierarchical_mmm/claude.md for the complete understanding of the codebase1_hierarchical_mmm. 
we will try to reuse codebase 1 as much as possible.

first 
inside snapshots/ucm folder create an understanfing .md file for each folder present in it so it that explains , or transcribes or summarizes that document. so that you do not need to see images again.
original images folder contains original unpartitioned images

these documents will be used as reference for the new approach we are trying to implement. below is reference guide
lte methodology and step by step guide contains on how to do lte modelling
fr pdx contains what and how it is reported  (it also contains technical details of the live example of lte modelling)
then code folders contains codes and ses png contain ses code.
data polation is the folder where we extrapolate data for last 6 months if we have only 3.5 of data i believe it is using forecast.ets() method of excel.

create a plan.md 

check best practices for UCM modelling while we are creating plan. this complete approach is a simple one but where ever you feel there is a way to better the process plan it.
we will try to use codebase 1 as much as possible .
note : - we are getting transformed media(adstock, hill & saturation) applied on the media data
i am trying to achieve to do the complete lte modelling which contains of the steps mentioned in the screenshot attached
in first step we take data of 3.5 or 4 years. (currently we have either 3.5 or 4 years of data but somecases could be where there is only 3 years of data. total required is 4 years)
so from observed variable which is the dependent variable (for eg sales volume) we do ucm extraction. which contains trend , seasonality , cyclicity, error term. (if you see ucm output you will notice there is Level which is actually close to observed variable and trend is basically telling the direction )
then some ses smoothing is done to get refined ucm trend.

then we do UCM Modelling
which is taking refined ucm trend as dependent variable and aggregated media data as independent variable for 3.5 years. (we will be using transformed media data for this step)
this is same nuts sampling

then the next step is nested modelling (this is in general case done on primary model done by vendor or by us first)
here we add 1 extra variable "ucm" as independent variable which captures contribution baseline. the period for this last 2 year this is also known as short term modelling. here dependent variable is sales volume.

then we retrofit the model
in it we have done nested model for latest 2 years. so to get contribution for earlier 2 years we take the coefficients from this latest 2 years and multiply it with support (transformed media and other data).
for ucm , ucm contribution to ucm trendline is taken for the nested model period then that calculated scaler, is used for earlier 2 years in respect to its own ucm trendline. this is how we get contribution for earlier 2 years. remember here ucm contribution is also taken proportionally from baseline factors.

then main calculation comes
we know from ucm modelling contribution% of a media variable to ucm trendline. so we take that contribution% and multiply it with the contribution% of the refined ucm trend from nested retrofit model (4 yrs). this gives us long term contribution% of that media variable. and from nested model we know short term contribution %
then we do final calculation

last is the wave analysis
in this we are trying to figure out long term impact of media variable on each year. so for example if tv done in year 1 then its impact will be seen in year 1,2,3,4. similarly for tv done in year 2 then its impact will be seen in year 2,3,4. so we are trying to figure out the long term impact of media variable on each year. basically the adstock effect.
this can be done simultaneously with with ucm modelling

your main guides are lte methodology and fr pdx docs.


also write what changes in web would be required to implement the new approach.




# Codebase 1: Hierarchical MMM — LTE Modelling Approach

## 1. Scope and Context

This conversation is strictly limited to **Codebase 1 and the implementation of the new LTE modelling approach within it**.

Before starting, read `codebase1_hierarchical_mmm/claude.md` thoroughly to understand the existing codebase, its architecture, modelling methodology, configuration, implementation details, and conventions.

Our primary objective is to **reuse Codebase 1 as much as possible**. Prefer extending or adapting existing components over creating new implementations. Avoid unnecessary duplication, architectural changes, or rewrites.

The new approach should integrate with the existing codebase wherever practical while maintaining its current design principles and functionality.

## 2. First Task: Document the Reference Materials

Inside the `snapshots/ucm/` directory, inspect every folder and its contents.

Create an `understanding.md` file inside each folder that explains, transcribes, or summarizes the relevant material contained in that folder. These files will serve as persistent reference documentation so that we do not need to repeatedly inspect the original images.

### Important instructions

- Inspect the existing directory structure before making changes.
- Create one `understanding.md` file for each folder present in `snapshots/ucm/`.
- Examine the original material carefully. Do not omit important equations, formulas, modelling assumptions, parameter values, workflows, implementation details, or reporting conventions.

- Preserve the original terminology wherever possible.
- Distinguish clearly between information explicitly stated in the source material and your own interpretations or assumptions.
- If something is unclear or unreadable, document the uncertainty instead of guessing or ask.


The `original images` folder contains the original, unpartitioned images. you can ignore this

### Reference folder guide

Use the following descriptions as an initial guide. Verify them against the actual directory contents rather than assuming they are complete or perfectly accurate.

**1. LTE methodology and step-by-step guide**

This contains the methodology and procedural instructions for LTE modelling. Treat it as one of the primary sources for understanding the intended workflow.

**2. FR PDX**

This contains information about what is reported, how results are reported, and the technical details of a live LTE modelling example.

Use it to understand the expected outputs, calculations, reporting conventions, and practical modelling implementation.

**3. Code folders**

These contain code related to the modelling process. 

**4. SES PNGs**

These contain screenshots or images related to SES (Simple Exponential Smoothing) code and implementation. 

**5. Data polation**

This folder contains the approach used to extrapolate data for the earlier six months when only approximately 3.5 years of observed data are available.

My current understanding is that this may use Excel's `FORECAST.ETS()` function. 


## 3. Second Task: Understand the Existing Codebase

After documenting the reference materials, analyze Codebase 1 in the context of the new LTE approach.


**Important modelling constraint:** The media data supplied to the LTE modelling process already has transformations applied, including adstock, Hill transformations, and saturation. Do not blindly apply these transformations again. Trace the existing data pipeline and establish exactly which representation of media data each modelling stage requires.

## 4. Third Task: Research UCM Modelling Best Practices

Research established best practices for Unobserved Components Models (UCM) and state-space time-series modelling.

Use this research to evaluate the proposed LTE methodology and identify opportunities to improve its robustness, accuracy, interpretability, and maintainability.


Evaluate proposed improvements against the business objective, the reference documents, the available data, and the existing implementation.

For every recommended change, explain:
1. The current approach.
2. The potential limitation or risk.
3. The proposed improvement.
4. The expected benefit.
5. The implementation cost and trade-offs.
6. Whether the change is essential or optional.

Keep the overall solution as simple as possible while maintaining statistical validity and reliable attribution.

## 5. LTE Modelling Workflow to Implement

The following describes the intended methodology. Treat the LTE methodology and FR PDX documents as the primary sources for confirming the exact calculations, equations, and business definitions.

If the source documents differ from this description, explicitly identify the discrepancy and resolve it in the plan rather than silently choosing one interpretation.

### Step 1: Prepare the historical data

The target historical period is four years.

Currently, the available data may cover:
- 3.5 years.
- 4 years.
- In some cases, only 3 years.

The intended process must account for these scenarios.

When only 3.5 years of data are available, the current approach appears to extrapolate the earlier six months. When only three years are available, determine whether the same method can be extended to estimate the missing year or whether a different strategy is required.


### Step 2: Extract the UCM components

Use the observed dependent variable, such as sales volume, to extract the components of an Unobserved Components Model.

The components of interest include:

- Level.
- Trend.
- Seasonality.
- Cyclicity.
- Irregular or error component.

Understand the distinction between the level and trend components. In the existing reference material, the level appears to track the observed series relatively closely, while the trend represents its underlying direction. 

### Step 3: Refine the extracted UCM trend using SES

Apply Simple Exponential Smoothing (SES) to the extracted UCM trend to obtain the refined UCM trend.


### Step 4: Perform UCM modelling

Use the **refined UCM trend as the dependent variable** and aggregated, transformed media data as the independent variables.

This stage uses the historical modelling period of approximately 3.5 years, subject to the exact rules in the reference documentation.

The media inputs already have transformations applied, including adstock, Hill transformations, and saturation.

This is the long-term or structural modelling stage used to estimate each media variable's contribution to the refined UCM trend.

The existing implementation appears to use NUTS sampling. 


### Step 5: Perform nested modelling

Nested modelling is generally performed on an existing primary model, which may have been developed by a vendor or internally.

In this approach, add one additional independent variable: the UCM.
here prior for other variable except baseline is locked so ucm only derives contribution from baseline variables.

Use the most recent two years as the modelling period. This is also referred to as short-term modelling.

The dependent variable is observed sales volume.
it is the same codebase1 modelling we are doing.



### Step 6: Retrofit the model to the full historical period

The nested model is fitted over the latest two years, but final reporting requires contributions across the full four-year period.

Retrofit the model to estimate contributions for the earlier two years.

**6.1. Media contributions**

Use the coefficients estimated over the latest two years and multiply them by the corresponding transformed media support for the earlier period, following the reference methodology.



**6.2. UCM contribution**

For the nested-model period, calculate the UCM contribution relative to the corresponding UCM trendline, using the exact definition in the reference materials.

Derive the appropriate scaler from the nested-model period and apply it to the earlier period relative to its own UCM trendline.


**6.3. Baseline factors**

Account for the proportional allocation of UCM contribution to baseline factors, as specified by the existing methodology.



### Step 7: Calculate long-term and short-term contributions

This is the central contribution calculation.

The UCM modelling stage provides the contribution percentage of each media variable to the refined UCM trend.

The nested retrofit stage provides the contribution percentage of the refined UCM trend within the final four-year sales attribution.

The intended long-term contribution calculation is:

**Long-term media contribution percentage**

= Media contribution percentage to the refined UCM trend

× Refined UCM trend contribution percentage in the nested retrofit model.

The nested model also provides the short-term contribution percentage.


### Step 8: Perform the final contribution calculations

Combine the long-term and short-term modelling outputs to generate the final LTE results.

Inspect the FR PDX reporting examples to establish exactly how the final metrics are calculated and presented.

Do not invent a final aggregation formula if it is not supported by the reference methodology. If the relationship between the long-term and short-term outputs is unclear, document the ambiguity and investigate it before implementation.

### Step 9: Perform wave analysis

Wave analysis estimates the long-term impact of media activity across subsequent years.

For example:
- TV activity in Year 1 may contribute to the impact observed in Years 1, 2, 3, and 4.
- TV activity in Year 2 may contribute to the impact observed in Years 2, 3, and 4.
- TV activity in Year 3 may contribute to the impact observed in Years 3 and 4.
- TV activity in Year 4 may contribute to the impact observed in Year 4.

The objective is to understand how media activity in each originating year contributes to outcomes in subsequent years.

This analysis is closely related to the carryover effect of adstocked media activity.

Investigate how the existing transformed media data and adstock implementation can support this calculation. Determine whether the required information can be derived from existing model outputs or whether the media transformation pipeline must retain additional intermediate data.

Wave analysis may be performed alongside UCM modelling if the dependencies permit it.


## 6. Fourth Task: Create `plan.md`

After completing the reference documentation, understanding Codebase 1, and evaluating UCM best practices, create a comprehensive `plan.md`.

The plan must describe how to implement the new LTE modelling approach within Codebase 1.

### Required sections in `plan.md`

1. **Executive summary** — Objectives, scope, and intended outcome.
2. **Existing codebase architecture** — Relevant modules, data flow, model components, configuration, and interfaces.
3. **Reference documentation summary** — Important findings from the LTE methodology, FR PDX, code folders, SES screenshots, and data extrapolation material.
4. **Current versus proposed workflow** — A step-by-step comparison of the existing implementation and the intended LTE approach.
5. **LTE modelling pipeline** — Detailed stages, dependencies, inputs, outputs, and calculations.
6. **Mathematical definitions** — Important equations, contribution formulas, scalers, and denominators, supported by the reference documents.
7. **UCM best practices and improvements** — Recommended changes, justifications, risks, and trade-offs.
8. **Data requirements and contracts** — Required fields, data granularity, transformations, time periods, intermediate outputs, and validation rules.
9. **Code reuse and change inventory** — Existing files or modules to reuse, modify, extend, or create. Reference actual paths and functions wherever possible.
10. **Model execution and orchestration** — Execution order, dependencies, sampling, intermediate artifacts, error handling, and reproducibility.
11. **Contribution calculations and reconciliation** — Long-term, short-term, baseline, retrofit, and final reporting calculations.
12. **Wave analysis design** — Mathematical approach, implementation dependencies, and outputs.
13. **Testing and validation strategy** — Unit tests, integration tests, synthetic-data tests, historical backtests, edge cases, and numerical invariants.
14. **Web application changes** — Frontend, backend, APIs, data contracts, visualizations, configuration, and reporting changes needed to expose the new approach.
15. **Risks and unresolved questions** — Missing information, conflicting definitions, assumptions requiring confirmation, and implementation risks.
16. **Implementation roadmap** — Phased implementation tasks with dependencies, expected deliverables, and acceptance criteria.


Do not make implementation changes yet unless explicitly required to document the existing code. The immediate deliverables are the `understanding.md` files and `plan.md`.

## 7. Web Application Changes

As part of the plan, determine what changes will be required in the existing web application to support LTE modelling.

## 9. Execution Order

Follow this sequence:

1. Read `codebase1_hierarchical_mmm/claude.md`.
2. Inspect the complete `snapshots/ucm/` directory structure and the original unpartitioned images.
3. Create an `understanding.md` file for every folder inside `snapshots/ucm/`.
4. Inspect the relevant parts of Codebase 1 and map its existing functionality to the proposed workflow.
5. Research UCM modelling best practices and evaluate potential improvements.
6. Resolve or document discrepancies between the reference material, the existing code, and the proposed methodology.
7. Create a comprehensive `plan.md`, including the implementation roadmap and required web application changes.
8. Review the documentation for completeness, consistency, and traceability.

**Final instruction:** Focus on understanding, documenting, and planning first. Do not begin implementing the new LTE modelling pipeline until the reference documentation and `plan.md` are complete and the key modelling ambiguities have been identified.


---------------------

Analyze Web thoroughly and create a `CLAUDE.md` file inside the `web/` folder that captures everything necessary to continue development seamlessly in a new Claude session.  Also consolidate all relevant context from this chat. . Verify implementation details against the actual web codebase, distinguish verified facts from assumptions and planned changes, and preserve important details that cannot be reconstructed from the code alone.  explicitly identify any context that could not be recovered or verified. This is all required as we will be moving towards Typescript or React whichever is better

<attach the image of dqm pipeline>

----------------


We need to remove FR PDX , and Colombia Dolex bmc name in the docs
agree with methodology
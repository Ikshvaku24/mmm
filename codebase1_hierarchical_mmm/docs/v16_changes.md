What v7 fixed
v6	v7
divergences	9	0
tree depth saturated	22.5%	0.0%
near-constant warnings	9	0
test r2_within_region	0.719	0.839
test MAPE	9.67%	6.59%
sampling time	523s	311s
dummy_feb_2026	−202.5%	−7.9% ✓
dummy_fixed_effects_*	−8.1% / −2.9%	0.0% / +1.7% ✓
The collinearity is genuinely gone and the model predicts materially better. That was worth doing.

Why the baseline gaps didn't move at all
sales_market_base_sales-volume went +30.4% → +30.5%. Not "barely improved" — identical. That's the tell: it was never a sampling problem.

From contribution_math.png, that feature in every region:


n_obs 104   raw_sum 104   center_used ≈ 1.00 (1.0039 / 0.9939 / 1.0020 / 0.9939)
scaled_sum_train 7.3e-15   effective_scaled_sum 104
base_sales-volume is an index that equals 1.0 every week. It is structurally an intercept. And dummy_fixed_effects_Core_Pharmacy / _Premium_Pharmacy have raw_sum = 104 in their own region and 0 elsewhere — they are region indicator dummies.

So your level space, for 4 regions, contains:

term	what it encodes
alpha_region (+ mu_alpha, tau_alpha)	4 free region levels
sales_market_base_sales-volume	a global 1.0 constant
dummy_fixed_effects_Core_Pharmacy	a region indicator
dummy_fixed_effects_Premium_Pharmacy	a region indicator
4 regions can identify at most 4 level parameters. The model fits 7. Only the priors decide how the level splits between them — the data cannot.

The arithmetic confirms it:


base_sales-volume    ours 2,157,093,311  vendor 1,636,913,686   +520,179,625
tdp                       1,823,866,606         2,010,927,046   -187,060,440
base-price               -1,060,265,261        -1,139,114,001    +78,848,740
brand_tpr                   394,460,844           480,325,023    -85,864,179
market_tpr                 -127,715,476          -162,483,859    +34,768,383
dummies                                                           +7,825,470
                                          NET BASELINE EXCESS  +368,697,599
                                         baseline_core_volume  -421,652,920
+368.7M of excess baseline, −421.7M of intercept. Same degree of freedom, opposite signs.

And the vendor has no such term — I checked: their contributions sum to 101.84% of actual with no intercept row at all. Their dummy_fixed_effects_* are their fixed effects.

Centering orthogonalises each feature against the intercept in the likelihood, which is why the sampler got clean. But contribution_reference=zero puts the level back for reporting, and the redundancy among alpha / base_sales-volume / fixed_effects was never touched by centering.

The fix is to remove the free intercept, matching the vendor's parameterisation:


ModelConfig(alpha_prior_sd=0.05, alpha_regional_sd=0.05, ...)
That pins alpha ≈ 0 and forces the level into the named baseline features, which is exactly how the vendor's model is specified. Right now mu_alpha posterior is +0.91 on a KPI scaled to mean 1.0 — your intercept alone is claiming ~91% of sales before any feature speaks.

Do this as an experiment and re-check: if the vendor's structure is right, the fit should hold (theirs closes at 101.8%) and base_sales-volume should fall back toward 1.64bn.

The two seasonality dummies — you diagnosed this correctly
You're right that it's prior_sd_basis, and it's worse than you thought. You set sign_constraint = negative (correct) but left prior_sd_basis = absolute. For a signed feature that means sigma = sqrt(log(1 + (0.2/mean)²)):

dummy	prior_mean	sigma_log	that's	if relative
dummy_2024end_core_seasonality	0.1108	1.2035	±180%	0.198
dummy_end2025_seasonality	0.1500	1.0107	±133%	0.198
dummy_feb_2026_priceincrease	0.1588	0.9748	±126%	0.198
A 90% prior interval of [−0.015 .. −0.802] — a factor of 3 either way. That prior says nothing, so the data decides, and with 4–6 non-zero weeks it says the effect is 40–55% smaller than the vendor's.

feb_2026 came right anyway (−202.5% → −7.9%) because there the sign constraint was the binding fix, not the width.

Set prior_sd_basis = relative on those three and they'll be held within ±20% of the vendor.

Media / BTL — unchanged, and it's a judgement call not a bug
btl_expert-calls −34%, btl_expert-samples −24%, media_digital-video_premium_hero −16%, media_tv_core_halo −16%, media_ecommerce-display +13%. All identical to v6.

These already have sigma = 0.198 (±20%). The data disagrees with the vendor by 1–2 prior sd, and a tight prior converts a 1-sd disagreement into a ~20% contribution gap by construction. Nothing is broken. Either tighten global_prior_sd to ~0.1 to hold them at the vendor's numbers, or accept the gap as your data's opinion. That's your call, not a fix.

Two smaller things
btl_shopper_unattr_invest_Grocery went −4.6% → −10.4%, and base-price_Core −15.5% → −18.2%, tdp_Premium_Grocery −10.9% → −13.3%. These are collateral from the level reshuffle — as base_sales-volume holds its excess, its neighbours give ground. They should improve when the intercept is pinned.
The _mat1/_mat2 and _newtarget/_oldtarget pairs still carry identical priors and remain partly collinear with each other. Compare each pair to the vendor as a sum, never individually.
What would help
I read everything in the folder, but two files would let me close the last loop:

02_convergence/prior_posterior_contraction.csv — the PNG is too dense to read at that resolution. I want contraction and mean_shift_in_prior_sd for v7 to confirm whether base_sales-volume is now prior-driven (contraction → 0, which is what I'd predict post-centering) or still data-driven.
01_data/model_input_summary.csv — the scaled sd of base_sales-volume after centering, to show how little variance is left for the coefficient to be estimated from.
CSVs rather than screenshots where you can — the OCR risk on these dense tables is real, and I'd rather not misread a number that matters.



Correction: base_sales-volume is not a constant
I inferred "an index equal to 1.0 every week" from raw_sum = 104 over 104 obs. That only fixes the mean. The actual column:

region	mean	sd	min	max
Core/Grocery, Premium/Grocery	1.0039	0.1590	0.733	1.664
Core/Pharmacy, Premium/Pharmacy	0.9939	0.0696	0.816	1.140
It varies ±16% in Grocery. It is a genuine regressor, not an intercept duplicate, and the contraction file confirms it's well identified: contraction 0.924, posterior sd 0.055 against a prior of 0.201. So the +31% is the data speaking, not an artefact. My "7 level parameters for 4 regions" framing was wrong for that feature.

The actual pattern — and it's a clean one
Sorting all 252 parameters by contraction against the comparison file gives one rule:

Features that match the vendor are the ones the data could NOT identify. Features that miss are the ones the data identified and disagreed with.

feature	contraction	%diff vs vendor
media_tv_premium_halo_*target	−0.374	−5.4% / −4.1% ✓
btl_shopper_premium_invest_Pharmacy	−0.363	+3.6% ✓
tdp_Core_Pharmacy	−0.288	+2.7% ✓
dummy_fixed_effects_Premium_Pharmacy	−0.078	+1.7% ✓
dummy_fixed_effects_Core_Pharmacy	−0.068	0.0% ✓
base_sales-volume	0.924 (shift +1.45)	+31% ✗
market_trade_tpr	0.723 (shift −1.23)	−21% ✗
trade_tpr_Core_Grocery	0.907 (shift −1.14)	−22% ✗
dummy_end2025_seasonality	0.880 (shift −0.92)	−55% ✗
dummy_2024end_core_seasonality	0.942 (shift −0.54)	−39% ✗
Negative contraction means the posterior came out wider than the prior — the likelihood said nothing, so the posterior is your prior, and your prior was derived from the vendor. Those match by construction, not by agreement. There are 43 such parameters, plus 70 more with contraction < 0.2.

So the honest reading of your comparison file is inverted from the intuitive one: the green cells are largely where the model learned nothing, and the red cells are where it learned something you don't like.

Where the level went

mu_alpha                        prior 0.031 -> posterior 0.911   contraction 0.964   shift +1.78 sd
alpha_region[Core,Grocery]            0.060 ->           0.860              0.9995         +1.15
alpha_region[Premium,Grocery]         0.012 ->           0.871              0.9995         +1.12
alpha_region[Premium,Pharmacy]        0.059 ->           0.983              0.9998         +1.32
alpha_region[Core,Pharmacy]           0.002 ->           1.028              0.9998         +1.51
Your KPI is scaled to mean 1.0, so the free intercept alone now explains 86–103% of average sales, pinned to ±0.01. The vendor's model has no such term — I verified their contributions close at 101.84% of actual with no intercept row; their dummy_fixed_effects_* are their fixed effects.

That is the one structural difference that survives. baseline_core = −13.1% and baseline_features = +109.7% are the accounting consequence: with contribution_reference=zero, each centred feature's beta × mean is moved out of the core and into the feature, so the core goes negative and the named features inflate.

But note the two dummy_fixed_effects_* have negative contraction — those are genuinely redundant against alpha_region, exactly as I suspected. The redundancy is real; it just isn't what's driving base_sales-volume.

The two seasonality dummies — the data disagrees, it isn't the prior width

dummy_end2025_seasonality        prior_mu -1.842 -> -2.727   contraction 0.880   post_sd 0.334
dummy_2024end_core_seasonality            -2.095 -> -2.718               0.942            0.278
dummy_feb_2026_priceincrease              -1.790 -> -1.857               0.039            0.971
You were right that prior_sd_basis=absolute gave them sigma ≈ 1.0–1.2 (±130–180%). But the consequence isn't what either of us assumed: contraction 0.88 and 0.94 means the data collapsed them anyway. They're data-driven, and the data says the effect is 41% and 54% of the vendor's magnitude.

feb_2026 is the opposite — contraction 0.039, purely prior-driven — which is why it now sits at −7.9%: the sign constraint put the prior on the right side of zero and the prior is the answer.

So switching those two to prior_sd_basis=relative would pull them to within ±20% of the vendor, but only by overriding a posterior that is ~2.5 sd away from the vendor's value. That's imposing the vendor's answer, not fixing a specification error. Your call — but it should be a deliberate one.

Media/BTL — weak likelihood, strong disagreement

btl_expert-calls      contraction 0.261   shift -2.21 sd    (PRIOR-DATA CONFLICT flagged)
btl_expert-samples                0.218         -1.45
digital-video_prem_hero           0.271         -0.98
tv_core_halo                      0.390         -0.99
Backing the likelihood out of btl_expert-calls: prior sd 0.191, posterior 0.164 ⇒ implied likelihood sd ≈ 0.32 centred near −4.0 against a prior at −2.38. The data thinks that coefficient is ~5× smaller than the vendor's. Your ±20% prior is holding it up, and the −34% gap is the compromise.

What I'd actually do
Nothing on media/BTL and the two seasonality dummies until you decide whose number you want. These are genuine data-vs-vendor disagreements, correctly reported. If the deliverable must reconcile to the vendor, tighten global_prior_sd to ~0.05–0.10 on them and say plainly that the contributions are prior-imposed.

Test the intercept. ModelConfig(alpha_prior_sd=0.05, alpha_regional_sd=0.05) reproduces the vendor's parameterisation (no free intercept). I'd run it — but I won't promise it fixes base_sales-volume. Forcing alpha → 0 makes the named features carry ~12% more level in total, which could push base_sales-volume further up rather than down. It's a diagnostic worth having, not a known fix.

Drop or merge the redundant pairs. media_tv_premium_halo_newtarget/_oldtarget (contraction −0.374 each) and digital-social_*_mat1/_mat2 carry identical priors and are unidentifiable against each other. Merge the columns, or report each pair only as a sum.

Stop reading the green cells as validation. Add contraction beside every row of your comparison. Anything below ~0.2 is your own prior echoing back — it agrees with the vendor because it is the vendor.

One caveat on my numbers: rows 138 and 184 of model_input_summary.md have the region and feature columns merged (('Premium', 'Groce_sales__market_...), so the Premium rows are a transcription artefact. Their values are identical to the matching Core rows, which is consistent with the feature being national-by-channel — but if you have the raw CSV I'd rather confirm than assume.
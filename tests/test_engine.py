"""Fictional, synthetic fixtures; no credit-union website data is included."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'harness'))
sys.path.insert(0, str(ROOT / 'submission'))
from contract import Page, Snapshot
from solution import build


def page(url, title, markdown, day):
    return Page(url=url, title=title, markdown=markdown, fetched_at=day)


class TemporalKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.engine = build()
        self.engine.ingest(Snapshot(
            snapshot_id='01_2026-01-10', taken_on='2026-01-10',
            pages=[
                page('https://example.test/rates/certificates', 'Certificate Rates',
                     '# Certificate Rates\n\n| Certificate | APY |\n|---|---|\n| 6 Month | 2.95% |\n| 12 Month | 3.10% |', '2026-01-10'),
                page('https://example.test/fees', 'Fees',
                     '# Fee Schedule\n\nLost debit card replacement fee: $2.50', '2026-01-10'),
            ]
        ))
        self.engine.ingest(Snapshot(
            snapshot_id='02_2026-03-01', taken_on='2026-03-01',
            pages=[
                page('https://example.test/rates/certificates', 'Certificate Rates',
                     '# Certificate Rates\n\n| Certificate | APY |\n|---|---|\n| 6 Month | 3.50% |\n| 12 Month | 3.10% |', '2026-03-01'),
                page('https://example.test/promo', 'Expired Sale',
                     '# Promotion\n\n6-month certificate APY 2.95% (expired 2025-09-01).', '2026-03-01'),
            ]
        ))

    def test_latest_rate_uses_authoritative_table(self):
        ans = self.engine.answer('What is the 6-month certificate APY?')
        self.assertIn('3.50%', ans.text)
        self.assertNotIn('2.95%', ans.text)
        self.assertEqual(ans.sources[0].url, 'https://example.test/rates/certificates')

    def test_as_of_date_selects_old_snapshot(self):
        ans = self.engine.answer('6-month certificate APY?', as_of='2026-02-01')
        self.assertIn('2.95%', ans.text)
        self.assertNotIn('3.50%', ans.text)

    def test_missing_tier_abstains(self):
        ans = self.engine.answer('What is the 60-month jumbo certificate APY?')
        self.assertFalse(ans.confident)

    def test_conflict_is_qualified_and_resolved(self):
        conflicts = self.engine.conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertIn('2.95%', conflicts[0].values)
        self.assertEqual(conflicts[0].resolution, '3.50%')

    def test_last_verified_removed_fee_is_labeled_historical(self):
        ans = self.engine.answer('How much is a lost debit card replacement fee?')
        self.assertIn('$2.50', ans.text)
        self.assertIn('Last documented', ans.text)

    def test_repeat_is_stable(self):
        q = '6 month certificate annual percentage yield?'
        self.assertEqual(self.engine.answer(q).text,self.engine.answer(q).text)


class QualifiedRateTests(unittest.TestCase):
    def setUp(self):
        self.engine = build()
        self.engine.ingest(Snapshot(
            snapshot_id='01_2027-01-01', taken_on='2027-01-01', pages=[
                page('https://fictional.test/personal/loans/auto-motorcycle-loans',
                     'Vehicle Loan Rates',
                     '# Vehicle Rates\n\n| Loan Program | Term | Annual Percentage Rate |\n'
                     '| --- | --- | --- |\n'
                     '| 2025-28 Model Year | 60 Months | 4.81% APR |\n'
                     '| 2025-28 Model Year | 84 Months | 5.11% APR |\n'
                     '| 2021-24 Model Year | 60 Months | 5.21% APR |', '2027-01-01'),
                page('https://fictional.test/calculators/auto-affordability',
                     'Illustrative Auto Payment',
                     '# Calculator\n\nThe illustrative 60-month auto loan APR is 4.61%.',
                     '2027-01-01'),
                page('https://fictional.test/personal/savings-account',
                     'Savings Rates',
                     '# Savings Rates\n\nShare Savings\n'
                     '| Account Type | Balance Tier | Dividend Rate | Annual Percentage Yield (APY) |\n'
                     '| --- | --- | --- | --- |\n'
                     '| Share Savings | $10,000 - $24,999.99 | 0.16% | 0.16% |\n'
                     '| Share Savings | $25,000+ | 0.21% | 0.21% |', '2027-01-01'),
                page('https://fictional.test/personal/loans/home-loans',
                     'Home Loan Rates',
                     '# Home Loans\n\n| Program | Rate | APR |\n'
                     '| --- | --- | --- |\n'
                     '| Purchase: 30-Year Fixed Conventional $100,000 or less | 6.13% | 6.28% |\n'
                     '| Purchase: 30-Year Fixed Conventional $900,000 or less | 6.25% | 6.32% |',
                     '2027-01-01'),
            ]
        ))

    def test_loan_cohort_term_and_authority(self):
        ans = self.engine.answer('What is the APR for a 60-month auto loan on a 2026 vehicle?')
        self.assertIn('4.81%', ans.text)
        self.assertNotIn('4.61%', ans.text)
        self.assertNotIn('5.21%', ans.text)
        self.assertIn('/auto-motorcycle-loans', ans.sources[0].url)

    def test_balance_tier_is_qualified(self):
        ans = self.engine.answer('What is the Share Savings APY for a balance between $10,000 and $24,999?')
        self.assertIn('0.16%', ans.text)
        self.assertNotIn('0.21%', ans.text)

    def test_mortgage_rate_amount_tiers_are_distinct(self):
        ans = self.engine.answer('What are the 30-year mortgage rates?')
        self.assertIn('6.13%', ans.text)
        self.assertIn('6.25%', ans.text)

    def test_unobserved_past_date_abstains(self):
        ans = self.engine.answer('What is the 60-month auto loan APR?',as_of='2026-12-01')
        self.assertFalse(ans.confident)



class AdversarialGroundingTests(unittest.TestCase):
    def setUp(self):
        self.engine=build()
        self.engine.ingest(Snapshot(snapshot_id='01_2027-02-01',taken_on='2027-02-01',pages=[
            page('https://fictional.test/personal/savings-account','Savings',
                 '# Savings\n\n| Account Type | Balance Tier | APY |\n|---|---|---|\n'
                 '| Share Savings | $10,000 - $24,999.99 | 0.17% |\n'
                 '| Share Savings | $25,000+ | 0.23% |\n'
                 '| Bright Savings | $0+ | 1.11% |','2027-02-01'),
            page('https://fictional.test/personal/checking-accounts','Checking',
                 '# Checking\n\nACH payments and direct deposits are available with checking accounts.','2027-02-01'),
            page('https://fictional.test/about/join','Join',
                 '# Join\n\nApply online at https://join.dupaco.com/apply.','2027-02-01'),
            page('https://fictional.test/newsroom/branch','Branch announcement',
                 '# Branch opening\n\n**Lobby** Monday–Friday 9 a.m.–5 p.m.','2027-02-01'),
        ]))

    def test_unknown_named_savings_product_must_abstain(self):
        ans=self.engine.answer('What is the APY for the Orion Galaxy Savings account?')
        self.assertFalse(ans.confident,ans.text)
        self.assertNotIn('0.17%',ans.text)

    def test_legitimate_named_savings_product_is_supported(self):
        ans=self.engine.answer('What is the rate for the Bright Savings account?')
        self.assertTrue(ans.confident,ans.text)
        self.assertIn('1.11%',ans.text)

    def test_rate_tier_percent_match_is_generic(self):
        found=self.engine.answer('Do any savings tiers pay 0.23% APY?')
        self.assertTrue(found.confident,found.text)
        self.assertIn('Yes.',found.text)
        missing=self.engine.answer('Do any savings tiers pay 0.18% APY?')
        self.assertTrue(missing.confident,missing.text)
        self.assertIn('No listed tier',missing.text)
        self.assertIn('0.23%',missing.text)

    def test_generic_checking_query_is_not_false_named_product(self):
        ans=self.engine.answer('Provide current checking account interest rates')
        self.assertNotIn('named savings or checking product',ans.text)

    def test_unknown_named_product_lowercase_abstains(self):
        ans=self.engine.answer('what is the apy for orion galaxy savings account?')
        self.assertFalse(ans.confident,ans.text)

    def test_personal_account_identifier_must_abstain(self):
        ans=self.engine.answer('What is the ACH account number of Alice Smith?')
        self.assertFalse(ans.confident,ans.text)
        self.assertFalse(ans.sources)

    def test_unverified_branch_hours_must_not_use_other_branch_news(self):
        ans=self.engine.answer('What are the Galena Branch lobby hours?')
        self.assertFalse(ans.confident,ans.text)

    def test_malformed_as_of_abstains(self):
        ans=self.engine.answer('Share Savings APY?',as_of='2027-99-99')
        self.assertFalse(ans.confident,ans.text)

    def test_outside_balance_bracket_abstains(self):
        ans=self.engine.answer('What APY on Share Savings account for a balance of $5?')
        self.assertFalse(ans.confident,ans.text)

    def test_page_citations_include_correct_snapshot(self):
        ans=self.engine.answer('How do I join online?')
        self.assertTrue(ans.confident,ans.text)
        self.assertEqual(ans.sources[0].snapshot_id,'01_2027-02-01')

    def test_missing_account_fee_is_non_confident(self):
        engine=build()
        engine.ingest(Snapshot(snapshot_id='01_2027-01-01',taken_on='2027-01-01',pages=[
            page('https://fictional.test/fees','Fees','# Fees\n\nLost debit card replacement fee: $2.50','2027-01-01')]))
        engine.ingest(Snapshot(snapshot_id='02_2027-02-01',taken_on='2027-02-01',pages=[
            page('https://fictional.test/home','Home','# Hello\n\nNew policies online.','2027-02-01')]))
        ans=engine.answer('How much is a lost debit card replacement fee?')
        self.assertFalse(ans.confident,ans.text)
        self.assertIn('Last documented',ans.text)
        self.assertEqual(ans.sources[0].snapshot_id,'01_2027-01-01')


class CrossSourceConflictTests(unittest.TestCase):
    def test_auto_calculator_vs_official_overlapping_cohort(self):
        engine=build()
        engine.ingest(Snapshot(snapshot_id='01_2027-01-01',taken_on='2027-01-01',pages=[
            page('https://fictional.test/personal/loans/auto-motorcycle-loans','Auto rates',
                 '# Rates\n\n| Loan Program | Term | APR |\n|---|---|---|\n'
                 '| 2025-28 Model Year | 60 Months | 4.81% APR |\n'
                 '| 2025-28 Model Year | 84 Months | 5.11% APR |','2027-01-01'),
            page('https://fictional.test/calculators/vehicle-affordability','Illustrative calculator',
                 '# Calculator\n\n| Account Type | Balance Tier | Annual Percentage Yield |\n|---|---|---|\n'
                 '| 2024-26 Model Year | 60 Months | 4.61% APR |\n'
                 '| 2029-30 Model Year | 84 Months | 4.91% APR |','2027-01-01'),
        ]))
        conflicts=engine.conflicts()
        relevant=[c for c in conflicts if 'auto loan APR' in c.topic]
        self.assertEqual(len(relevant),1,[(c.topic,c.values) for c in conflicts])
        self.assertEqual(set(relevant[0].values),{'4.81%','4.61%'})
        self.assertEqual(relevant[0].resolution,'4.81%')
        self.assertEqual(len(relevant[0].sources),2)


if __name__ == '__main__':
    unittest.main()
import { LandingHero } from "@/components/landing/LandingHero";
import { InteractivePlanner } from "@/components/landing/InteractivePlanner";
import { AlternativesSection, BudgetAware, FinalCTA, GroupSection, ProofBand, WhyDifferent } from "@/components/landing/Sections";
import { PricingSection } from "@/components/PricingSection";
import { Footer } from "@/components/Footer";

export default function Home() {
  return (
    <main>
      <LandingHero />
      <InteractivePlanner />
      <ProofBand />
      <WhyDifferent />
      <BudgetAware />
      <AlternativesSection />
      <GroupSection />
      <PricingSection />
      <FinalCTA />
      <Footer />
    </main>
  );
}
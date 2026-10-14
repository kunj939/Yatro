import { LandingHero } from "@/components/landing/LandingHero";
import { InteractivePlanner } from "@/components/landing/InteractivePlanner";
import { FinalCTA, GroupSection, WhyDifferent } from "@/components/landing/Sections";
import { PricingSection } from "@/components/PricingSection";
import { Footer } from "@/components/Footer";

export default function Home() {
  return (
    <main>
      <LandingHero />
      <InteractivePlanner />
      <WhyDifferent />
      <GroupSection />
      <PricingSection />
      <FinalCTA />
      <Footer />
    </main>
  );
}
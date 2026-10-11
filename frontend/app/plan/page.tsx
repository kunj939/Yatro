import type { Metadata } from "next";
import { Navbar } from "@/components/Navbar";
import { TripWizard } from "@/components/wizard/TripWizard";

export const metadata: Metadata = { title: "Plan my trip" };

export default function PlanPage() {
  return (
    <main className="min-h-screen">
      <Navbar />
      <TripWizard />
    </main>
  );
}

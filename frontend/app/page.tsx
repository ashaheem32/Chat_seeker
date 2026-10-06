/**
 * Landing page (/).
 *
 * Pure marketing / explainer surface shown before users enter the app.
 * Composition only — every section lives in components/landing/* and
 * animates itself (scroll reveals, canvas particles, SVG draws, marquees).
 * The upload flow that used to live here is now at /upload.
 */

import { Features } from "@/components/landing/Features";
import { FinalCta } from "@/components/landing/FinalCta";
import { Footer } from "@/components/landing/Footer";
import { Hero } from "@/components/landing/Hero";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { LandingNav } from "@/components/landing/LandingNav";
import { Languages } from "@/components/landing/Languages";
import { PlatformMarquee } from "@/components/landing/PlatformMarquee";
import { Privacy } from "@/components/landing/Privacy";

export default function LandingPage() {
  return (
    <div className="relative min-h-screen overflow-x-clip scroll-smooth">
      <LandingNav />
      <main>
        <Hero />
        <PlatformMarquee />
        <HowItWorks />
        <Features />
        <Languages />
        <Privacy />
        <FinalCta />
      </main>
      <Footer />
    </div>
  );
}

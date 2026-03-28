import { DemoHeroGeometric } from "@/components/ui/demo";
import { DashboardClient } from "@/components/dashboard/dashboard-client";

export default function Home() {
  return (
    <main className="relative min-h-screen bg-[#09090b] text-zinc-100 overflow-x-hidden">
      <DemoHeroGeometric />
      <div className="relative z-10 -mt-10 md:-mt-16 pb-24">
        <DashboardClient />
      </div>
    </main>
  );
}


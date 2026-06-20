// components/HeroCanvas.tsx
// The iridescent glass torus-knot from the design, as a self-contained React
// component. Loads three.js from CDN via next/script, then renders a
// transmission/iridescence material lit by violet + cyan point lights, with
// mouse-parallax lerp. Cleans up fully on unmount (disposes GL context).

"use client";

import { useEffect, useRef } from "react";
import Script from "next/script";

declare global {
  interface Window {
    THREE?: any;
  }
}

export function HeroCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const ready = useRef(false);

  function init() {
    if (ready.current) return;
    const THREE = window.THREE;
    const canvas = canvasRef.current;
    if (!THREE || !canvas) return;
    ready.current = true;

    const parent = canvas.parentElement!;
    const w = parent.clientWidth || window.innerWidth;
    const h = parent.clientHeight || window.innerHeight;

    const renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(w, h, false);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.15;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 100);
    camera.position.set(0, 0, 6);

    // Equirectangular gradient env map → colorful glass reflections.
    const c = document.createElement("canvas");
    c.width = 1024;
    c.height = 512;
    const g = c.getContext("2d")!;
    const grad = g.createLinearGradient(0, 0, 0, 512);
    grad.addColorStop(0, "#ffffff");
    grad.addColorStop(0.35, "#dfe3ff");
    grad.addColorStop(0.6, "#a78bfa");
    grad.addColorStop(0.8, "#6366f1");
    grad.addColorStop(1, "#22d3ee");
    g.fillStyle = grad;
    g.fillRect(0, 0, 1024, 512);
    const spot = (x: number, y: number, r: number, col: string) => {
      const rg = g.createRadialGradient(x, y, 0, x, y, r);
      rg.addColorStop(0, col);
      rg.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = rg;
      g.fillRect(0, 0, 1024, 512);
    };
    spot(250, 150, 280, "rgba(255,255,255,0.9)");
    spot(800, 380, 300, "rgba(34,211,238,0.55)");
    const envTex = new THREE.CanvasTexture(c);
    envTex.mapping = THREE.EquirectangularReflectionMapping;
    envTex.colorSpace = THREE.SRGBColorSpace;
    const pmrem = new THREE.PMREMGenerator(renderer);
    const envRT = pmrem.fromEquirectangular(envTex);
    scene.environment = envRT.texture;
    envTex.dispose();

    const geo = new THREE.TorusKnotGeometry(1.1, 0.36, 280, 44);
    const mat = new THREE.MeshPhysicalMaterial({
      color: new THREE.Color("#cdd0ff"),
      metalness: 0.12,
      roughness: 0.04,
      transmission: 0.55,
      thickness: 1.5,
      ior: 1.45,
      clearcoat: 1,
      clearcoatRoughness: 0.05,
      iridescence: 1,
      iridescenceIOR: 1.35,
      envMapIntensity: 1.5,
      attenuationColor: new THREE.Color("#a78bfa"),
      attenuationDistance: 3.5,
    });
    const mesh = new THREE.Mesh(geo, mat);
    scene.add(mesh);

    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const p1 = new THREE.PointLight(0x8b5cf6, 80);
    p1.position.set(-5, 3, 4);
    scene.add(p1);
    const p2 = new THREE.PointLight(0x22d3ee, 65);
    p2.position.set(5, -3, 3);
    scene.add(p2);
    const d1 = new THREE.DirectionalLight(0xffffff, 1.3);
    d1.position.set(2, 4, 5);
    scene.add(d1);

    const mouse = { x: 0, y: 0 };
    const target = { x: 0, y: 0 };
    const onMove = (e: MouseEvent) => {
      target.x = (e.clientX / window.innerWidth) * 2 - 1;
      target.y = (e.clientY / window.innerHeight) * 2 - 1;
    };
    window.addEventListener("mousemove", onMove);

    const onResize = () => {
      const W = parent.clientWidth || window.innerWidth;
      const H = parent.clientHeight || window.innerHeight;
      camera.aspect = W / H;
      camera.updateProjectionMatrix();
      renderer.setSize(W, H, false);
    };
    window.addEventListener("resize", onResize);

    const clock = new THREE.Clock();
    let raf = 0;
    const animate = () => {
      raf = requestAnimationFrame(animate);
      const t = clock.getElapsedTime();
      mouse.x += (target.x - mouse.x) * 0.05;
      mouse.y += (target.y - mouse.y) * 0.05;
      mesh.rotation.y = t * 0.25 + mouse.x * 0.5;
      mesh.rotation.x = t * 0.15 + mouse.y * 0.4;
      mesh.rotation.z = t * 0.04;
      renderer.render(scene, camera);
    };
    animate();

    // Cleanup closure stored on the canvas for the effect's return.
    (canvas as any).__cleanup = () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("resize", onResize);
      try {
        geo.dispose();
        mat.dispose();
        pmrem.dispose();
        envRT.dispose();
        renderer.dispose();
        renderer.forceContextLoss?.();
      } catch {}
    };
  }

  useEffect(() => {
    // If three loaded before mount, init now.
    if (window.THREE) init();
    const canvas = canvasRef.current;
    return () => {
      (canvas as any)?.__cleanup?.();
      ready.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <Script
        src="https://unpkg.com/three@0.160.0/build/three.min.js"
        strategy="afterInteractive"
        onLoad={init}
      />
      <canvas
        ref={canvasRef}
        style={{
          position: "absolute",
          inset: 0,
          width: "100%",
          height: "100%",
          display: "block",
          pointerEvents: "none",
        }}
      />
    </>
  );
}
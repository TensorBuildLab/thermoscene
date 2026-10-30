---
title: From energy models to thermal scenes: an ontology-driven pipeline for physically grounded thermography of buildings and HVAC systems
author: Liang Zhang | 1 | corresponding
author: Huanrui Yang | 1
author: TBD student author | 1
author: Yiyuan Jia | 2
affiliation: 1 | University of Arizona, Tucson, AZ, USA
affiliation: 2 | Big Ladder Software, Denver, CO, USA
email: liangzhang1@arizona.edu
note: Author list tentative; to be confirmed.
keywords: building energy simulation; EnergyPlus; infrared thermography; HVAC topology; fault detection; synthetic training data
bibliography: references.ris
endnote-style: Numbered
---

# Highlights {-}

- EnergyPlus models become 3D thermal scenes of envelopes and HVAC systems
- Name-free HVAC tracing matched declared systems: precision 1.00, recall 0.998
- Every pixel carries a true temperature, a camera reading and a provenance level
- A duct gap was visible but cost 1% of cooling; a wall void raised conduction ~20%
- Runs unchanged on an unmodified reference office building, all 15 zones

# Abstract {-}

Thermography reveals envelope and heating, ventilation and air-conditioning (HVAC) faults that waste energy, and drones and robots increasingly carry thermal cameras, but automated interpretation lacks labelled data that link images to building operation. This paper presents thermoscene, an open pipeline that turns an existing EnergyPlus model into a three-dimensional thermal scene of a chosen zone, its envelope and the HVAC system serving it, with thermal images whose pixels carry a true temperature, a camera reading and a provenance level. A name-free tracer recovers the equipment serving the zone from the model's node graph. Generators add diffusers, ducts, piping, terminal boxes, air-handling units and chillers, sized from EnergyPlus autosizing and bound to simulated nodes, so their surface temperatures follow simulated fluids. Envelope and duct-insulation faults are injected so that their signatures and their energy consequences come from the same simulation. Against the system structure declared in twelve EnergyPlus example models (180 zones), the tracer reached a precision of 1.000 and a recall of 0.998, and scenes were built for every zone of an unmodified U.S. Department of Energy reference office. Physics-based element models agreed with standard heat-transfer correlations within about 1 K. Per-frame provenance showed envelope and plenum views to be mostly simulated, whereas ceiling and rooftop views relied on heuristics. Over a year, a duct-insulation gap was visible almost whenever air flowed but added 1 % to the zone's cooling, whereas an envelope void raised its wall's conduction by about a fifth while its contrast reversed on most days.

# Introduction {#sec:s1}

Infrared thermography is the standard tool for finding the envelope and heating, ventilation and air-conditioning (HVAC) faults that waste energy in buildings: missing insulation, thermal bridges, air leakage, moisture, unwrapped ducts and pipes, failing valves and malfunctioning terminal units [@kylili2014infrared; @fox2014thermography; @lucchi2018applications; @kirimtat2018review; @martin2022infrared]. Quantitative thermography turns thermograms into in-situ U-values and thermal-bridge heat losses [@tejedor2017quantitative; @ogrady2017quantification; @nardi2018quantification], and drones and ground robots now carry thermal cameras for energy audits [@rakha2018review; @bayomi2021building; @halder2023robots]. The bottleneck is interpretation. Detectors for envelope anomalies and rooftop thermal bridges are trained on manually labelled thermograms [@park2021thermal; @mayer2023deep; @mayer2023thermal], HVAC distribution components have only begun to be inspected this way [@guan2022automatic], faults are rare, and a measured thermogram reveals neither the energy consequence of what it shows nor whether a given contrast comes from a fault or from the time of day.

Building energy simulation already computes much of what a thermal camera sees. Engines such as EnergyPlus [@crawley2001energyplus] produce time-resolved surface temperatures, zone conditions and the temperatures and flows at every node of the HVAC system; EnergyPlus can simulate operational faults [@zhang2017modeling]; labelled fault datasets for fault detection and diagnosis (FDD) exist as time series [@kim2018review; @granderson2023labeled]; and national model collections cover entire building stocks [@new2021modelamerica]. These models, however, are not scenes. Thermal zones are coarse shells, HVAC equipment exists only as a node graph without dimensions or location, and nothing a camera would see above a ceiling or on a roof is represented. Semantic building models such as Brick [@balaji2016brick] and ASHRAE 223P [@ashrae2026standard223p] describe system topology but neither geometry nor thermal state, and building information models (BIM) describe geometry but rarely operation [@kamel2019bim]. Conversely, robot simulators provide geometry and sensors, but their thermal cameras show temperatures that users assign to objects by hand [@gazebo2026thermal], and their scene collections contain no HVAC systems [@savva2019habitat; @deitke2022procthor].

This paper connects the two. We present **thermoscene**, an open pipeline that turns an existing EnergyPlus model into a three-dimensional thermal scene: an OpenUSD stage in which walls, windows, ceilings, diffusers, ducts, pipes, valves, air-handling units (AHUs), chillers and roofs each carry a time series of surface temperature, with simulated thermal-camera images and per-pixel labels. The user names a zone; the pipeline reads its geometry, simulates the building, traces the HVAC system that serves the zone, generates the physical elements EnergyPlus does not contain, assigns temperatures to them, and renders thermal images. Faults are injected into the model or into the generated elements, so that their thermal signatures and their energy consequences come from the same simulation. The resulting synthetic thermography is intended for developing and testing automated thermographic diagnosis of buildings and HVAC systems, whether the camera is handheld, mounted on a drone or carried by a ground robot.

Three requirements shaped the design. First, the pipeline must work on models it has never seen, without model-specific names or edits; this rules out hand-built mappings from EnergyPlus objects to geometry. Second, every quantity that is not simulated is an assumption, and a user training or evaluating a diagnosis method must be able to tell which is which, pixel by pixel. Third, the output must be usable both by building analysts and by vision and robotics tools.

**Scope.** This paper addresses physically grounded thermal scenes and the labelled observations that a thermal camera would record in them, at any pose and any simulated time, together with the energy consequence of the faults they contain. The scenes operate in replay mode: thermal states are precomputed from the simulation and do not respond to an observer's actions. Validation against field thermography, closed-loop robot simulation and benchmark perception tasks are outside the scope of this paper; @sec:s6-2 sets out what remains.

The contributions are:

1. An ontology for thermal scenes of buildings that separates simulated components from generated physical elements, binds each element to the EnergyPlus node whose fluid it carries, and labels every quantity, and every pixel of every image, with one of six provenance levels under explicit decision rules.
2. A generic HVAC topology tracer that recovers the chain of equipment serving a zone, including plant equipment behind water coils, from the node graph of an EnergyPlus model without assuming object or node names, verified component by component against each model's declared system structure.
3. A rule-based generation and thermal-state layer: air devices, plenum distribution and rooftop equipment sized from EnergyPlus autosizing, with surface temperatures derived from simulated fluid temperatures, benchmarked against standard heat-transfer correlations, and with envelope and duct-insulation faults injected so that EnergyPlus or the element models compute their signatures.
4. A thermal image formation model in the 8–14 µm band, shared by a CPU reference renderer and a real-time browser viewer, producing per-pixel ground-truth temperatures, simulated camera readings and per-frame provenance maps.
5. An evaluation on a system-level case, on an unmodified U.S. Department of Energy (DOE) reference building and every zone of it, and on twelve EnergyPlus example models covering eight HVAC system types, including a one-year comparison of how visible each injected fault is with what it does to the building's heat flows.

@sec:s2 reviews related work. @sec:s3 describes the method. @sec:s4 presents the case studies and evaluation design, and @sec:s5 the results. @sec:s6 discusses implications and limitations, and @sec:s7 concludes.

# Related work {#sec:s2}

No existing approach produces three-dimensional scenes of a building and its HVAC system whose thermal state follows from building simulation. @tbl:t1 positions this work against three adjacent lines of research.

## Infrared thermography of buildings and HVAC systems {#sec:s2-1}

Thermography is established for envelope diagnostics and energy audits [@kylili2014infrared; @fox2014thermography; @lucchi2018applications], and recent reviews cover its use from components to cities [@kirimtat2018review; @martin2022infrared]. Its interpretation depends on emissivity, reflected apparent temperature and the thermal history of the surface: metallic and foil-faced surfaces reflect their surroundings, and the contrast of a defect depends on weather and time of day [@vollmer2018infrared]. Quantitative thermography estimates U-values from interior thermograms [@tejedor2017quantitative] and the heat loss of thermal bridges from exterior ones, which depends strongly on wind [@ogrady2017quantification]; its error sources are reviewed in [@nardi2018quantification]. Drones carry thermal cameras for envelope audits [@rakha2018review], and aerial thermograms have been used to calibrate the envelope of an energy model [@bayomi2021building], the opposite direction to this work. Robotic inspection platforms increasingly carry thermal cameras as well [@halder2023robots]. Automated interpretation uses convolutional networks for wall anomalies [@park2021thermal] and detectors for rooftop thermal bridges trained on a public drone dataset of 926 manually annotated images [@mayer2023deep; @mayer2023thermal]. For HVAC distribution, an image-based method diagnoses damaged pipe insulation and leaks, with a camera on an inspection robot or at a fixed position in mind [@guan2022automatic]. All these methods depend on measured, labelled thermograms. Synthetic thermal images of buildings have been produced by translating aerial RGB images [@hou2021novel] and by physics-based infrared simulation of urban scenes with assumed interior conditions [@bulatov2020from]; general-purpose infrared scene simulators such as DIRSIG compute radiometry for remote sensing [@goodenough2017dirsig5]. None of them derives the temperatures of envelopes and concealed HVAC equipment from an energy model of the building's operation.

## Building energy models, faults and semantic models {#sec:s2-2}

Whole-building energy simulation computes heat balances of surfaces and zones and the operation of HVAC plants [@crawley2001energyplus]. Reference and prototype buildings [@deru2011reference] and urban-scale model generation such as Model America [@new2021modelamerica] make these models available for whole building stocks. EnergyPlus models operational faults and their energy impact [@zhang2017modeling], and FDD for commercial buildings has a large literature [@kim2018review] with public labelled datasets of faulted and fault-free operation [@granderson2023labeled]; these are time series, without an image counterpart. Digital twins link building and HVAC models to operational data for energy management and predictive maintenance [@arowoiya2024digital; @hosamo2022digital]. BIM-to-BEM (building energy model) workflows translate geometry from BIM into energy models [@kamel2019bim; @andriamamonjy2018automated], again the opposite direction. Semantic schemas such as Brick [@balaji2016brick], ASHRAE 223P [@ashrae2026standard223p] and SAREF-based frameworks [@bjornskov2023ontology] describe equipment, points and connections. Brick models have been built from design and simulation artefacts [@fierro2020shepherding] and inferred from time series [@benfer2024semantic], and a knowledge-graph framework generates energy models and establishes their HVAC topology from building information [@wang2024knowledge]. EnergyPlus itself reports the nodes and branches of every loop it simulates (eplusout.bnd) [@energyplus2022bnd], but not the chain of equipment that serves a given zone. Time-series datasets organised with Brick support analytics [@prabowo2024building], and synthetic images rendered from BIM have been used to train segmentation of mechanical, electrical and plumbing (MEP) components [@baubriaud2023mepseg]. None of these couples the physical geometry of the HVAC system with its simulated thermal state.

## Simulation environments for building control and for embodied AI {#sec:s2-3}

Reinforcement-learning and benchmarking frameworks wrap building simulation as control environments: BOPTEST [@blum2021boptest], Sinergym [@jimenezraboso2021sinergym] and the Smart Buildings Control Suite [@goldfeder2024smart]; learned dynamics models, including diffusion-based world models, have been proposed for HVAC control [@yang2026adapt]. These environments expose zone-level states and actions to a controller and have no spatial scene. Embodied-AI research trains navigation and manipulation policies in simulated indoor scenes, scanned [@savva2019habitat; @ramakrishnan2021hm3d] or procedurally generated [@kolve2017ai2thor; @deitke2022procthor; @raistrick2024infinigen], with object states for household tasks [@srivastava2022behavior] and GPU physics and sensor simulation on OpenUSD [@mittal2023orbit]; generative world models synthesise scenes from images or text [@nvidia2025cosmos]. These scenes contain no HVAC systems or concealed services, and thermal cameras in robot simulators show user-assigned object temperatures [@gazebo2026thermal]. The closest thermal effort is an object-level RGB–thermal–3D dataset for temperature-aware manipulation [@su2026hearth]. Synthetic data with domain randomisation is the usual remedy for scarce labels in robotics [@tobin2017domain], but it needs a source of physically plausible temperatures.

Table: Capabilities of related approaches (✓ provided, ◐ partial, – absent). {#tbl:t1}

| Approach | 3D scene | HVAC / MEP geometry | Simulated thermal state | System topology | Provenance of values | Works from existing building models |
| --- | --- | --- | --- | --- | --- | --- |
| Measured thermogram datasets [@mayer2023thermal] | – | – | ◐ (measured, envelope) | – | – | – |
| Physics-based infrared scene simulation [@bulatov2020from; @goodenough2017dirsig5] | ✓ | – | ✓ (exterior) | – | – | ◐ |
| Embodied-AI simulators [@savva2019habitat; @deitke2022procthor; @raistrick2024infinigen] | ✓ | – | – | – | – | – |
| Building control environments [@blum2021boptest; @goldfeder2024smart] | – | – | ✓ (zone level) | ◐ | – | ✓ |
| BIM / semantic models [@balaji2016brick; @wang2024knowledge; @baubriaud2023mepseg] | ◐ | ✓ (BIM) | – | ✓ | – | ◐ |
| **thermoscene (this work)** | ✓ | ✓ (generated) | ✓ envelope, ◐ generated elements | ✓ | ✓ | ✓ |

# Methodology {#sec:s3}

## Overview {#sec:s3-1}

The pipeline has five stages that share one ontology (@fig:f1). Its inputs are an EnergyPlus model in its input data file (IDF) format, a weather file, the name of one zone and a short case configuration in YAML. The configuration is deep-merged over a defaults file that holds the sizing and layout rules, material properties and thermal-model parameters, so that the main assumptions live in one place. The outputs are an OpenUSD stage, a Resource Description Framework (RDF) graph of the scene, a JSON summary and, optionally, rendered thermal images.

![Stages of the pipeline. Each stage reads and writes instances of the ontology (bottom), never model-specific names.](../docs/paper/figures/fig1_pipeline.png){#fig:f1 width=6.5in}

## Ontology and provenance {#sec:s3-2}

The ontology distinguishes what EnergyPlus simulates from what the pipeline generates (@fig:f2). A **Space** is a volume with its own thermal environment: a conditioned zone, a plenum or the outdoors. A **Surface** is an EnergyPlus heat-transfer surface; an **Opening** is a sub-surface cut out of it (window, door or injected defect). A **SimComponent** is an EnergyPlus HVAC object, kept as data only, with **Ports** bound to EnergyPlus nodes and a medium (air or water). An **Element** is a visible object generated for the scene, such as a duct segment, pipe, valve, diffuser or equipment casing. Each Element may *represent* one or more SimComponents and, when a fluid flows inside it, carries a *stream node*: the EnergyPlus node of that fluid. A **Stream** is the temperature and mass-flow time series at that node. A **Camera** observes one Space, which supplies the reflected and background temperatures, and a **Fault** affects a Surface or an Element.

![Ontology of a thermal scene. SimComponents (data) and Elements (geometry) are linked by *represents*; Elements are bound to simulated fluid conditions through their stream node.](../docs/paper/figures/fig2_ontology.png){#fig:f2 width=6.5in}

Every quantity of every instance carries one of six provenance levels, assigned by the following rules: *simulated*, an EnergyPlus output used as it is; *physics*, a value derived from simulated inputs by a first-principles relation (conduction, convection, radiation) with tabulated property values; *engineering rule*, a value from design practice, such as a design velocity; *heuristic*, a value from an empirical relation or with at least one uncalibrated parameter that changes it, including assumed material properties and the relaxation of stagnant fluid; *assumed layout*, a position chosen by a rule; and *user*, a value given in the configuration. Levels are written into the USD stage next to each attribute, counted in the summary and exported to RDF. They describe the true temperature, which is the label of a thermal image; the camera reading additionally depends on the assumed emissivity and the reflected temperature (@sec:s3-8). Classes map to Brick [@balaji2016brick] and ASHRAE 223P terms, so the scene graph can be queried with existing building-metadata tools.

## Model adaptation and simulation {#sec:s3-3}

The user's model is never edited in place. A minimal IDF tool copies it, replaces its run periods and time step, and appends what the pipeline needs: output variables requested with wildcard keys (surface inside and outside face temperatures, zone mean air and radiant temperatures, node temperatures and mass flows, equipment part-load ratios, heat-rejection and electricity rates, and site sky temperature, dew point and solar irradiance), an SQLite output, the requested time step and run periods. When run periods are given, weather-file runs are switched on and design-day runs off in SimulationControl, since several reference models ship with the opposite setting. Faults that EnergyPlus must simulate are injected at this point (@sec:s3-6-4).

The prepared model is simulated, with ExpandObjects when it contains HVAC templates. Runs are cached by a SHA-256 hash of the prepared model, the weather file and the EnergyPlus executable, so scenes for many zones of one building share a single simulation; the EnergyPlus version is recorded in every scene. The expanded model is converted to epJSON, whose named fields make the readers independent of field order and EnergyPlus version. Results are read from SQLite on a common time axis that keeps weather-file run periods, drops design days and HVAC sub-steps, and prefers time-step frequency when a variable was also requested hourly by the original model. As in EnergyPlus, each time stamp marks the end of a zone time step; rates are averages over the step that ends there.

## Spaces and surfaces {#sec:s3-4}

The target zone's surfaces are read in world coordinates, honouring GlobalGeometryRules (relative coordinates, zone origins and rotation, clockwise vertex entry). Windows, doors and injected defects become Openings and are cut out of their host as polygon holes before triangulation, so any planar polygon is supported. The plenum is detected through the zone's ceiling: if its boundary condition is another surface and that surface's zone is listed in an AirLoopHVAC:ReturnPlenum or SupplyPlenum, or is unconditioned and excluded from the building's floor area, that zone becomes a Plenum space. Coincident interzone faces are each moved 5 mm into their own space so that each side renders its own temperature. Outdoor-facing surfaces can also receive an exterior skin that carries the outside-face temperature and the outside-layer emissivity; inside faces use the thermal absorptance of the innermost layer, which EnergyPlus uses as long-wave emissivity. Glazing takes the infrared emissivity of the facing side of its WindowMaterial:Glazing layer; SimpleGlazingSystem windows carry no layer data and get 0.84, the value of uncoated glass, so a low-emissivity coating is represented only when the window is described layer by layer.

## Generic HVAC topology tracing {#sec:s3-5}

The tracer builds a directed graph whose vertices are EnergyPlus nodes, components and zones, and recovers the equipment serving a zone without any object or node names.

1. **Components and ports.** Every object outside an exclusion list (controllers, setpoint and availability managers, sizing, schedules, output and energy-management objects, branch, loop and list containers) whose fields reference nodes becomes a SimComponent. A field is a port if its name contains *node* and *inlet* or *outlet*, which also gives its direction; sensor, set-point, control and reference nodes are ignored. Node lists are expanded, and one override table handles objects whose field names carry no direction (the outdoor-air mixer's *mixed* and *stream* nodes).
2. **Edges.** An inlet port gives an edge node → component; an outlet port, component → node. Components sharing a node are therefore connected.
3. **Implicit links.** Supply and demand sides of AirLoopHVAC, PlantLoop and CondenserLoop are joined node to node, marking demand-to-supply links as return links. Connector:Splitter and Connector:Mixer reference branches rather than nodes, so the last outlet node of each inlet branch is linked to the first inlet node of each outlet branch. Each zone becomes a pseudo-component between its inlet and return nodes (ZoneHVAC:EquipmentConnections).
4. **Medium and role.** Nodes on branches listed by plant or condenser loops carry water; all others carry air. Branch-list membership assigns roles (air supply, plant supply, plant demand, condenser supply or demand), air distribution units mark terminal units, and zone equipment lists mark zone equipment. A component may hold several memberships; a cooling coil, for instance, is air-loop supply equipment and chilled-water demand. Children of containers (a direct-expansion (DX) coil inside CoilSystem:Cooling:DX, coils inside fan-coil units) inherit the container's memberships.
5. **Serving chain.** A breadth-first search walks upstream from the zone. It stops at other zones and does not follow plant return links, so parallel branches serving other zones are not collected while the supply equipment of every loop feeding the zone is. For each water coil, a second search returns its plant sources (chillers, boilers, district or water-heater sources).

Object types are mapped to ontology classes by a prefix table (e.g. AirTerminal:SingleDuct:VAV → VAVTerminalUnit, Coil:Cooling:DX → DXCoolingCoil, Chiller and DistrictCooling → Chiller). @fig:f3 shows the chain recovered for case study B.

To check the tracer independently of the node graph it walks, a reference chain is assembled for every zone from the objects that declare the system structure, again without names: the zone's equipment list, its air distribution units and terminals (with the coils and fans they reference), the air loop whose supply path feeds the terminal, with its supply branches, outdoor-air equipment list, supply path and return path, and, for every water-side component found, the supply side of the plant or condenser loop whose demand branches contain it. The two chains are compared component by component (@sec:s5-3).

![Serving chain recovered by the tracer for case study B, drawn as flow lanes (logical plant pipes contracted). Air passes from the return plenum through the AHU to the terminal unit and the zone; the chilled-water and hot-water loops feed the cooling and reheat coils.](../docs/paper/figures/fig3_topology_caseB.png){#fig:f3 width=6.5in}

## Generation of physical elements {#sec:s3-6}

Generators add the Elements that EnergyPlus does not contain. They read the traced chain, the zone's air connections, autosized values from the ComponentSizes table, the scene's geometry and the rules in the defaults file (@tbl:t2). Every Element records the provenance of its geometry, size and material.

Table: Generation rules and default parameters. {#tbl:t2}

| Element group | Rule | Default parameters | Provenance |
| --- | --- | --- | --- |
| Diffuser count | @eq:e1, from the terminal design flow per zone instance | $\dot V_{\max}$ = 0.15 m³/s, $A_{\max}$ = 25 m², 0.6 m face | engineering rule |
| Diffuser position | k-means centres of the ceiling polygon inset by 0.6 m | — | assumed layout |
| Return grilles | ⌊n/2⌋ (≥ 1) at the interior points farthest from all diffusers and previously placed grilles | 0.6 m face | assumed layout |
| Round ducts, pipes | smallest standard size with velocity ≤ design velocity | round ducts 4 m/s, largest 600 mm; pipes 1.2 m/s (plenum), 1.8 m/s (roof); standard steel pipe series | engineering rule |
| Rectangular ducts | @eq:e2, side ≥ 0.15 m, height ≤ 40 % of plenum depth | plenum main 6 m/s, 30 % of the air-loop design flow; rooftop return 7 m/s, 85 % of it, height ≤ 0.5 m | engineering rule |
| Plenum layout | mains along the longest non-exterior zone edge; terminal box between main and diffusers; orthogonal routes | clearances 0.25–0.6 m | assumed layout |
| AHU / packaged unit | one casing section per air-loop supply component in flow order, plus discharge; coil face area from the face velocity, plus 0.30 m (width) and 0.35 m (height) for the casing | 2.5 m/s; minimum 1.2 × 0.9 m; section lengths 0.5–1.6 m | engineering rule |
| Plant sources | sources of the AHU's water coils: air-cooled chillers on the roof; water-cooled chillers, boilers and district sources in a plant room, piped from a riser; cooling towers traced, not drawn | chiller length 1.6 m + 0.02 m/kW | assumed layout |
| Materials | by element class and location (plenum, roof, zone) | e.g. foil-faced duct wrap ε = 0.30, aluminium pipe jacket ε = 0.12, bare galvanised steel ε = 0.25 | heuristic (assumed properties) |

### Air devices {#sec:s3-6-1}

The number of supply diffusers follows from the design airflow of one zone instance and the ceiling area:

$$ n_d = \max\left(\left\lceil \frac{\dot V_{z}}{\dot V_{\max}} \right\rceil,\; \left\lceil \frac{A_c}{A_{\max}} \right\rceil,\; 1\right) $$ {#eq:e1}

where $\dot V_z$ is the zone design flow, the terminal unit's autosized maximum flow divided by the zone multiplier, $A_c$ the ceiling area and $\dot V_{\max}$ and $A_{\max}$ the flow and area served by one diffuser (@tbl:t2). Each diffuser is bound to a zone inlet node with a flow share, so its face temperature follows the simulated supply-air condition. Return grilles are added only when the zone has return nodes.

### Plenum distribution {#sec:s3-6-2}

When a plenum exists and the chain contains an air terminal unit, a main duct is laid along the longest edge of the zone footprint that is not an exterior wall. Its cross-section follows from the design flow $\dot V$ and velocity $v$, with $h_{\min}$ = 0.15 m and $h_{\max}$ = 40 % of the plenum depth:

$$ h = \max\left(h_{\min},\; \min\left(h_{\max},\; \sqrt{\frac{\dot V}{2v}}\right)\right), \qquad w = \max\left(h_{\min},\; \frac{\dot V}{v\,h}\right) $$ {#eq:e2}

A terminal box sized from the zone design flow sits between the main and the diffuser cluster, with a reheat section when the chain contains a heating coil whose air outlet is a zone inlet. Discharge ducts run orthogonally from the box to each diffuser and drop vertically to its neck; each run is sized for its own diffuser's share. Hot-water supply and return pipes, a two-way control valve and exposed coil return bends are generated only if that reheat coil is a water coil. A return opening is added when the plenum is a return plenum. Stream nodes come from the ports: the main and branch carry the terminal's air inlet node, the box its damper outlet node; the reheat section and discharge ducts the zone inlet node; the pipes the coil's water inlet and outlet nodes.

### Rooftop and plant equipment {#sec:s3-6-3}

The AHU, or packaged unit, is built from the air-loop supply components of the chain, sorted topologically by flow: one casing section per component plus a discharge section above the shaft. A DX cooling coil adds a condenser section with fans, turning the AHU into a packaged unit. An outdoor-air louver is placed at the mixing box, and a return duct runs from the shaft to the mixer's return inlet. The plant sources of the AHU's water coils are placed by type; cooling towers and the sources of reheat coils are traced but not drawn. Air-cooled chillers sit on the roof beside the AHU, with insulated supply and return pipes, a bare control valve at the coil and a pump motor on the supply pipe; sources placed in a plant room are piped from a riser. Roofs come from the model's own roof surfaces or from a configured box.

### Faults {#sec:s3-6-4}

Two fault mechanisms keep fault signatures physically computed. An **envelope defect** is injected before simulation as an opaque sub-surface (Door) on the host surface, given by a rectangle in the host's surface coordinates. Its construction copies the host construction with the highest-resistance layer replaced by an air cavity (0.17 m²K/W by default), so EnergyPlus computes its inside and outside temperatures like any other surface. A **duct insulation gap** replaces a segment of a generated duct with bare galvanised steel and switches its thermal model accordingly. EnergyPlus FaultModel objects (coil fouling, sensor offsets, etc.) are passed through unchanged.

## Thermal state {#sec:s3-7}

Surfaces take the simulated inside- or outside-face temperature. Spaces take their air and mean radiant temperatures; the outdoors takes air, sky and dew-point temperatures and horizontal irradiance, computed from direct-normal and diffuse irradiance with the solar position at the middle of each step. Elements take a fluid temperature from their stream node. When the flow at that node stops, the trapped fluid relaxes towards the surrounding air:

$$ T_{f,i} = T_{a,i} + \left(T_{f,i-1} - T_{a,i}\right) e^{-\Delta t/\tau} \quad \text{if } \dot m_i = 0 $$ {#eq:e3}

where $T_f$ is the fluid and $T_a$ the surrounding air temperature in time step $i$, $\Delta t$ the time step and $\tau$ the time constant: 45 min for air in a wrapped duct, 120 min for water in an insulated pipe and 30 min for water in a bare valve body. These are rounded lumped-capacitance estimates (heat capacity of fluid, wall and insulation over the conductance to the plenum) for the default sizes (@sec:s5-6); because one value per medium serves all sizes, the relaxation remains heuristic. Surface temperatures are then computed by a registry of models (@tbl:t3). The generated elements exchange heat that EnergyPlus does not see, so the coupling is one-way; @sec:s5-7 quantifies the largest such term. For insulated ducts and pipes:

$$ T_s = T_a - \left(T_a - T_f\right)\frac{1/h_o}{1/h_i + \delta/k + 1/h_o}, \qquad T_s = T_a - \left(T_a - T_f\right)\frac{1/h_o}{r_2 \ln(r_2/r_1)/k + 1/h_o} $$ {#eq:e4}

where $T_s$ is the surface temperature, $h_i$ (20 W/m²K) and $h_o$ (8 W/m²K in the plenum, 17 W/m²K outdoors) the inner and outer film coefficients, $\delta$ the insulation thickness, $k$ its conductivity (0.040 W/(m·K)), $r_1$ and $r_2$ the pipe and jacket radii, and $T_a$ the plenum air temperature indoors or, outdoors, the sol-air temperature:

$$ T_{\mathrm{sa}} = T_o + \frac{\alpha f_s I_h}{h_o} - \frac{\varepsilon h_r f_{\mathrm{sky}}\left(T_o - T_{\mathrm{sky}}\right)}{h_o} $$ {#eq:e5}

where $T_o$ and $T_{\mathrm{sky}}$ are the outdoor air and sky temperatures, $I_h$ the horizontal irradiance, $\alpha$ and $\varepsilon$ the solar absorptance and emissivity of the surface, $h_r$ = 5 W/m²K the radiative coefficient and $f_s$ and $f_{\mathrm{sky}}$ the solar and sky view factors (0.6 and 0.5 for equipment, 1 for roof decks).

Condenser coils and fans take the discharge-air temperature rise from the simulated heat rejection $\dot Q_{\mathrm{rej}}$ (the chiller's condenser heat transfer; for DX coils, cooling plus electric power) and the condenser airflow $\dot V_c$ (autosized for chillers, 0.114 m³/s per kW of capacity for DX units), with $\rho$ = 1.2 kg/m³, $c_p$ = 1005 J/(kg·K) and the coil fraction $\phi_c$ = 0.6:

$$ \Delta T_c = \frac{\dot Q_{\mathrm{rej}}}{\rho\, \dot V_c\, c_p}, \qquad T_{\mathrm{fan}} = T_o + \Delta T_c, \qquad T_{\mathrm{coil}} = T_o + \phi_c \Delta T_c $$ {#eq:e6}

The diffuser face temperature $T_d$ moves from the ceiling temperature $T_c$ towards the supply-air temperature $T_{\mathrm{sup}}$ with the flow fraction $\varphi = \dot m/\dot m_{\mathrm{des}}$, and its attached jets cool or warm the surrounding ceiling by $\Delta T_j$:

$$
\begin{aligned} T_d &= T_c + \left(T_{\mathrm{sup}} - T_c\right)\eta_{\max}\varphi^{n} \\ \Delta T_j(x,y) &= \left(T_d - T_c\right) a_{\max}\sqrt{\varphi}\, e^{-r/(L_0 + L_1\varphi)} \left(0.6 + 0.4\cos^2 2\theta\right) \end{aligned}
$$ {#eq:e7}

where $r$ is the distance beyond the edge of the square diffuser face, $\theta$ the direction from its centre and $\varphi$ is limited to 1.2; by default $\eta_{\max}$ = 0.85, $n$ = 0.25, $a_{\max}$ = 0.5, $L_0$ = 0.15 m and $L_1$ = 0.45 m. The jet field is evaluated per pixel at the hit point, so it needs no geometry of its own.

Table: Thermal models for generated elements. {#tbl:t3}

| Model | Applied to | Inputs | Equation | Provenance |
| --- | --- | --- | --- | --- |
| insulated_flat | duct wrap, lined boxes, coil sections | stream node, insulation thickness | @eq:e4, left | physics |
| insulated_pipe | insulated pipes | stream node, outer diameter, insulation | @eq:e4, right | physics |
| bare_duct | insulation gaps | stream node | film resistances | physics |
| bare_pipe | valves, coil bends | stream node | fixed 3 % film-resistance ratio (water side dominant) | heuristic |
| sol_air | generated roof decks | irradiance, sky temperature, material | @eq:e5 | heuristic |
| casing, louver | AHU sections, rooftop return duct, outdoor-air louver | stream node, coupling 5–8 %; louver: 30 % of the sol-air excess | sol-air + coupling | heuristic |
| condenser_coil, condenser_fan | chiller and packaged condensers | simulated heat rejection, condenser flow | @eq:e6 | heuristic |
| compressor, motor | compressor enclosures, pump motors | part-load ratio (PLR), pump power | +12 K × PLR; +15 K when running | heuristic |
| diffuser_face, jets | supply diffusers, ceiling | supply node, flow fraction, ceiling temperature | @eq:e7 | heuristic |
| return_grille, ambient | grilles, return openings | return node, space air | mixing / air temperature | heuristic / physics |

## Scene output and thermal image formation {#sec:s3-8}

The scene is written as an OpenUSD stage whose layout mirrors the ontology: /World/<Space>/Surfaces, /World/<Space>/Elements, /World/Systems for SimComponents (data prims with ports, roles and autosized values) and /World/Cameras. Relationships encode *represents*, *connectedTo*, *locatedIn*, *hostedBy* and *feeds*. Time series are stored as USD time samples, one per EnergyPlus zone time step, or, for long runs, in a compressed NumPy sidecar referenced from each attribute. All meshes carry collision properties for use in robot simulators.

Thermal images are formed in the 8–14 µm band. The band radiance of a blackbody at temperature $T$ is

$$ L_b(T) = \int_{8\,\mu\mathrm{m}}^{14\,\mu\mathrm{m}} \frac{2hc^2}{\lambda^5}\,\frac{1}{e^{hc/(\lambda k_B T)} - 1}\,d\lambda $$ {#eq:e8}

The radiance reaching the camera from a surface with emissivity $\varepsilon$ is $L = \varepsilon L_b(T_s) + (1 - \varepsilon) L_b(T_r)$, where the reflected temperature $T_r$ is the mean radiant temperature of the observed space indoors and the mean of sky and air temperature outdoors. Like a real camera, the simulated camera inverts this with its own settings, emissivity $\varepsilon_c$ and reflected temperature $T_{\mathrm{rc}}$:

$$ T_{\mathrm{app}} = L_b^{-1}\left(\frac{L - (1 - \varepsilon_c) L_b(T_{\mathrm{rc}})}{\varepsilon_c}\right) + \text{blur} + \mathcal{N}(0,\, \mathrm{NETD}^2) $$ {#eq:e9}

with $\varepsilon_c$ = 0.95, $T_{\mathrm{rc}}$ = 20 °C, a 0.7-pixel Gaussian blur and a noise-equivalent temperature difference (NETD) of 0.05 K by default, the settings of an untuned camera; every camera can be given its own settings. Low-emissivity surfaces such as foil-faced duct wrap or aluminium pipe jackets are therefore misread, as in practice [@vollmer2018infrared].

The reference renderer ray-casts the stage on the CPU and writes three per-pixel outputs per camera and time step: the true surface temperature (the label), the apparent temperature (the sensor reading) and a provenance map. Provenance is evaluated per frame. Each pixel starts from the level of the surface it shows; a ceiling pixel becomes heuristic where the jet field of @eq:e7 changes its temperature by at least 0.1 K (twice the NETD), and an element becomes heuristic at time steps when its fluid is stagnant, since its temperature then rests on @eq:e3. The same shading module serves an Isaac Sim script, which renders segmentation and depth once per camera and applies the shading for every requested time step; it follows the documented Isaac Sim interface but has not been executed on GPU hardware, so only its shading path is verified (@sec:s5-6). Cameras are placed explicitly or automatically: candidate positions on a grid inside the space (or on a ring outdoors) are scored by the visible share of the target object (the first element or surface of the configured class), capped to avoid close-ups, plus a small bonus for the number of distinct objects in view. Because poses are unconstrained, a camera can stand for a handheld camera, a sensor on a ground robot, a drone surveying the roof, a crawler in the plenum or a fixed installation.

# Case studies and evaluation design {#sec:s4}

The method is evaluated on two case studies and five further evaluations (@tbl:t4). Case study B exercises every part of the pipeline on a deliberately complete HVAC system; case study C applies it, unchanged, to a reference building the authors did not write. Unless stated otherwise, runs use EnergyPlus 22.2, two weekday periods (14–15 January and 15–16 July, results reported for the second day), four time steps per hour and a 320 × 240 pixel, 8–14 µm camera; the one-year analysis of E7 reruns cases B and C over the full typical meteorological year (TMY3) of their locations. An earlier run of the same cases with EnergyPlus 25.2 gave identical element counts, topology results and sensitivity values, and morning-reheat temperatures of case B that differed by up to 1.3 K.

## Case study B: an office and its complete HVAC system {#sec:s4-1}

The model is a 5 m × 4 m × 3 m corner office with south and west exterior walls, a 2.0 m × 1.3 m south window and a 0.9 m return plenum, in Phoenix, Arizona (TMY3). The wall is 25 mm stucco, 90 mm insulation and 12.7 mm gypsum. It is served by a central variable-air-volume (VAV) system defined with EnergyPlus HVAC templates: an AHU with a chilled-water coil and economiser, a 72 kW air-cooled chiller, a 33 kW gas boiler and a VAV terminal with hot-water reheat (30 % minimum flow). Equipment is autosized to Phoenix's 0.4 % cooling (43.4 °C dry bulb) and 99.6 % heating (3.7 °C) design days. A zone multiplier of 40 represents the office as one of forty identical offices on the same AHU, so that the plant has a realistic size (3.72 m³/s supply air); the simplification also makes all forty offices peak together, so case B tests the pipeline on a complete system rather than modelling a particular building. A packaged-DX variant of the same office (case A) is included in the repository. Two faults are configured: an 0.8 m × 0.8 m insulation void in the south wall, injected into the model and simulated by EnergyPlus, and an 0.18 m gap in the insulation of the duct that drops to the supply diffuser. Four cameras observe the wall, the ceiling, the plenum and the roof; the plenum camera is placed automatically on the terminal box. Because only this floor is modelled, the roof that carries the rooftop equipment is a configured 15 m × 12 m box at 7.8 m, large enough for the AHU and the chiller.

Beyond the four fixed cameras, the case B scene is also exported as an interactive environment: a self-contained browser page in which the user moves a virtual thermal camera freely, as a ground robot or drone would, scrubs or plays the simulated period, changes the camera's emissivity and reflected-temperature settings, and switches between camera readings, true temperatures and provenance. It uses the same per-pixel radiometry as the reference renderer (@sec:s3-8), evaluated in a GPU shader in real time; because the free camera belongs to no single space, each surface reflects the mean radiant temperature of its own space, and optical blur is omitted.

## Case study C: an unmodified DOE reference building {#sec:s4-2}

The model is the DOE reference medium office for Chicago shipped with EnergyPlus (RefBldgMediumOfficeNew2004_Chicago.idf) [@deru2011reference], used without any modification: three floors, 18 zones of which 15 are conditioned, one packaged VAV unit per floor with DX cooling and gas heating, and VAV terminals with electric reheat. Besides the fault, the camera targets and the storage format, the configuration names only the zone, the top-floor south perimeter zone Perimeter_top_ZN_1 (a 49.9 m × 4.6 m trapezoid), and asks for the model's own roof surfaces to carry rooftop equipment. Plenum, roof geometry, serving chain and all elements are derived; all four cameras (window, diffuser, plenum and roof) are placed automatically; an insulation void of 1.0 m × 0.8 m is injected in the south wall; time series are stored in the sidecar format.

## Evaluation {#sec:s4-3}

Table: Evaluations and what they test. {#tbl:t4}

| ID | Evaluation | Input | Question |
| --- | --- | --- | --- |
| E1 | System-level scene | case study B | Are generated elements, sizes and thermal signatures coherent across envelope, terminal, plenum and roof? |
| E2 | Unmodified reference model | case study C | Does the pipeline work on a model it has never seen, with no edits and automatic cameras? |
| E3 | Whole-building robustness | all 15 conditioned zones of case C | Does every zone of a multi-storey building, core and perimeter, with and without roof, produce a valid scene? |
| E4 | Topology correctness | 12 EnergyPlus example models, 8 HVAC system types, 180 zones | Does the tracer recover, component by component, the equipment each model declares for each zone? |
| E5 | Label provenance | all cameras of B and C, every rendered frame | Which share of each image's pixels shows simulated, physics-derived or heuristic temperatures, and how does it change in time? |
| E6 | Sensitivity | case B, 15 parameters, 8 apparent contrasts | How much do the non-simulated parameters change the contrasts a camera reads, one at a time and in a global screening? |
| E7 | Energy relevance of faults | case B (two days and one year), case C | How visible is each injected fault, and what does it do to the building's heat flows? |

E6 varies each of fifteen non-simulated parameters (insulation conductivity, plenum film coefficient, stagnant-water time constants of the insulated pipe and the bare valve, envelope cavity resistance, diffuser effectiveness, ceiling-jet amplitude and decay length, duct-wrap emissivity, motor rise, condenser-coil fraction, AHU casing coupling, sol-air solar fraction, and the camera's emissivity and reflected-temperature settings) between illustrative bounds around its default, and recomputes eight apparent-temperature contrasts across the faults, the terminal, the ceiling jets and the rooftop equipment. One-at-a-time variation is complemented by a Morris elementary-effects screening [@morris1991factorial] with ten trajectories on a four-level grid, which captures interactions [@tian2013review; @menberg2016sensitivity]. Rebuilding a scene re-uses the cached EnergyPlus run, except when the parameter enters the model (the cavity resistance). E7 compares, for both faults, the apparent contrast a camera would read with the change in heat flow the fault causes: EnergyPlus inside-face conduction through the void against the same area of insulated wall, and the heat picked up by the supply air through the duct gap against the office's sensible cooling. The physics-level element models are benchmarked against standard correlations, the one-dimensional defect against a two-dimensional conduction model, and the renderers against each other (@sec:s5-6).

# Results {#sec:s5}

## E1: system-level scene (case study B) {#sec:s5-1}

From the office, the tracer recovered 17 components: the VAV terminal and its hot-water reheat coil, the zone splitter, supply fan, chilled-water coil, outdoor-air mixer and return plenum of the AHU, and the pumps, chiller, boiler and logical pipes of both plant loops (@fig:f3). The generators turned this chain into 28 elements (@tbl:t5, Fig. [-@fig:f4]a–b). All sizes follow from autosized values: the 1.12 m³/s main duct (30 % of the 3.72 m³/s air loop) is 0.61 m × 0.30 m, the terminal inlet and discharge ducts are 200 mm, the AHU cross-section is 1.64 m × 1.46 m and the chilled-water piping is nominal diameter DN50 for the chiller's 2.58 kg/s.

Table: Elements generated for case study B (sizes from EnergyPlus autosizing and @tbl:t2 rules). {#tbl:t5}

| Location | Elements | Sizes | Stream node (fluid inside) | Thermal model |
| --- | --- | --- | --- | --- |
| Zone ceiling | supply diffuser, return grille | 0.6 m faces; 0.093 m³/s per zone instance | zone inlet / return node | diffuser_face + jets, return_grille |
| Plenum, air | main duct, branch, terminal box, reheat section, discharge duct, return opening | 0.61 × 0.30 m main; 200 mm branch and discharge; 0.50 × 0.45 × 0.30 m box | terminal inlet, damper outlet, zone inlet nodes | insulated_flat, ambient |
| Plenum, water | hot-water supply and return pipes, control valve, coil return bends | DN15 (21 mm outer diameter) + 25 mm insulation | reheat coil water inlet / outlet nodes | insulated_pipe, bare_pipe |
| Roof, air | AHU (4 sections), outdoor-air louver, return duct, roof deck | 1.64 × 1.46 m sections, 3.72 m³/s; 0.95 × 0.48 m return duct | component outlet nodes, AHU return node | casing, louver, sol_air |
| Roof, plant | chiller compressor, condenser coil, 2 condenser fans, chilled-water supply and return pipes, control valve, pump motor | 72 kW; DN50 (60 mm outer diameter) + 38 mm, aluminium jacket | chiller evaporator outlet / inlet, coil water outlet | compressor, condenser_coil, condenser_fan, insulated_pipe, bare_pipe, motor |
| Fault | unwrapped duct segment | 0.18 m on the diffuser drop | zone inlet node | bare_duct |

The thermal images (@fig:f5) and time series (@fig:f6) show signatures that follow the simulated operation of the system rather than a fixed pattern.

- **Envelope.** The injected insulation void is 2.8 K colder than the insulated wall at 07:00 in winter (17.2 vs 20.0 °C), 2.9 K warmer at 15:00 when the sun-heated stucco conducts inward, and 5.6 K warmer on the summer afternoon (35.3 vs 29.7 °C). The sign of the contrast reverses within a day.
- **Terminal and diffuser.** The same diffuser is invisible when the system is off (23.9 °C, equal to the ceiling at 22:00), warm during morning reheat (23.1 vs 21.2 °C at 07:30, supply air 23.7 °C), cool at minimum flow (16.7 vs 21.9 °C at 09:00) and 10.8 K below the ceiling at full flow on the summer afternoon (17.7 vs 28.5 °C).
- **Plenum.** During reheat the uninsulated control valve reaches 64.3 °C while the jacket of the insulated pipe stays at 26.7 °C. After reheat stops, the trapped water cools: the valve still reads 31.6 °C at 10:00. The unwrapped duct segment is 6.4 K colder than the wrapped duct on the summer afternoon and 1.5 K warmer during reheat.
- **Roof.** On the 44.4 °C summer afternoon the roof membrane reaches 63.7 °C and the condenser fans 53.7 °C, 9.3 K above ambient from the simulated heat rejection. The chiller was autosized to the 43.4 °C design day; on this hotter afternoon it runs at its capacity limit and the chilled-water supply drifts from its 6.7 °C setpoint to 9.4 °C at 15:00. This state is a consequence of the model's sizing, not a fault, and the scene reproduces it because it comes from the simulation. At 03:00 on the winter night the roof is 4.1 K colder than the air through sky radiation.

![Generated geometry. (a) Case B office and plenum (cut-away): main duct, branch, terminal box with reheat section, discharge drop with the unwrapped segment (red) and hot-water piping. (b) Case B roof: AHU sections, air-cooled chiller with condenser fans, chilled-water piping and pump. (c) Case C perimeter zone and plenum with nine diffuser runs. (d) Case C packaged rooftop unit on the model's own roof.](../docs/paper/figures/fig4_geometry.png){#fig:f4 width=6.5in}

![Case study B: per-pixel provenance of the right-hand frame (left column) and simulated camera readings at two instants for the wall, ceiling, plenum and roof cameras. Colour scales are set per panel (1st to 99.5th percentile), so contrasts should not be compared across panels by colour.](../docs/paper/figures/fig5_thermal_caseB.png){#fig:f5 width=6.5in}

![Case study B, true temperatures: (a) envelope defect and wall, (b) diffuser, ceiling, supply air and room supply flow (grey, right axis), (c) plenum valve, pipe and ducts, (d) rooftop equipment and roof on the summer day; the bare chilled-water (CHW) valve sits on the coil's water outlet. HW, hot water.](../docs/paper/figures/fig6_timeseries_caseB.png){#fig:f6 width=6.5in}

## E2: unmodified reference model (case study C) {#sec:s5-2}

Given the zone name, the pipeline found the top-floor plenum and the roof, traced the zone's packaged VAV system (terminal with electric reheat, splitter, fan, gas heating coil, DX cooling coil inside a CoilSystem:Cooling:DX container, mixer, return mixer and plenum) and generated 37 elements (Fig. [-@fig:f4]c–d). The 1.2 m³/s design flow of the 49.9 m long zone gave nine diffusers with 250 mm runs and four return grilles. The air loop, which serves five zones at 7.41 m³/s, became a packaged rooftop unit with a 165 kW condenser section. No hot-water piping was generated because the reheat coil is electric. All four automatically placed cameras framed their targets (@fig:f7).

The simulated signatures differ from case B because the climate and the system differ. On the Chicago winter night (−14.4 °C outside, 15.6 °C setback), the injected void is 5.4 K colder than the wall (8.1 vs 13.5 °C), and the roof's outside face, taken directly from the EnergyPlus roof surface, is colder than the air through sky radiation. During morning warm-up the electric reheat drives the diffuser face to 34.9 °C, 18.4 K above the ceiling, the reverse of its 16.6 °C summer-afternoon state.

![Case study C (unmodified DOE reference medium office): per-pixel provenance of the right-hand frame and simulated camera readings of the automatically placed window, diffuser, plenum and roof cameras. Colour scales are set per panel.](../docs/paper/figures/fig7_thermal_caseC.png){#fig:f7 width=6.5in}

## E3–E4: robustness and generality {#sec:s5-3}

**Whole building.** Scenes were built for all 15 conditioned zones of the reference building with one shared EnergyPlus run (@tbl:t6). Every build succeeded. The three 983 m² core zones receive 40 diffusers and 20 grilles each, all served by the single terminal unit the model defines per zone. Rooftop equipment appears for the five top-floor zones only, because only their scenes contain roof surfaces. With the cached simulation, a perimeter-zone scene takes 1.9–3.3 s and a core-zone scene 5.4–6.8 s on one laptop CPU core (Intel Core i7-1185G7).

Table: Zone sweep over the unmodified DOE reference medium office. {#tbl:t6}

| Zone group | Zones built | Surfaces | Traced components | Diffusers / grilles | Generated elements | Rooftop elements |
| --- | --- | --- | --- | --- | --- | --- |
| Core (bottom, mid, top) | 3 / 3 | 21–24 | 10 | 40 / 20 | 105–115 | 0 (top: 10) |
| Perimeter, bottom and mid floors | 8 / 8 | 27 | 10 | 7–9 / 3–4 | 22–27 | 0 |
| Perimeter, top floor | 4 / 4 | 24 | 10 | 8–9 / 4 | 35–37 | 10 |

**HVAC topology.** Applied without configuration to twelve EnergyPlus example models, the tracer recovered a serving chain for all 180 conditioned zones (@tbl:t7). Compared component by component with the chains declared by the models' own system objects (@sec:s3-5), it found no component that the model does not declare for the zone (precision 1.000) and missed 6 of the 2,503 declared zone–component pairs (recall 0.998); 174 of 180 chains are identical. The six misses are zone exhaust fans in the outpatient building: they draw air out of their zones, so they are not upstream of them and lie outside a serving chain by construction. The models span central VAV with air- and water-cooled chillers and cooling towers, packaged VAV with DX cooling, packaged single-zone units, four-pipe fan coils, packaged terminal air conditioners and heat pumps, and the mixed systems of an 86-zone outpatient building. Water-cooled systems were traced through the condenser loop to the cooling towers, and DX coils nested in container objects were recognised as air-loop equipment.

Table: Topology tracing on EnergyPlus example models (no model-specific configuration), compared with the chains declared by each model's system objects. {#tbl:t7}

| Model | Zones (conditioned) | Traced zones | Mean chain length | Precision / recall | Identical chains | Main classes found |
| --- | --- | --- | --- | --- | --- | --- |
| 5ZoneAirCooled | 6 (5) | 5/5 | 20.0 | 1.000 / 1.000 | 5/5 | VAV, CHW coil, HW reheat, chiller, boiler, pumps |
| RefBldgSmallOffice | 6 (5) | 5/5 | 8.0 | 1.000 / 1.000 | 5/5 | single-duct terminal, DX coil, heating coil |
| RefBldgMediumOffice | 18 (15) | 15/15 | 10.0 | 1.000 / 1.000 | 15/15 | VAV, DX coil, gas and electric heating |
| RefBldgLargeOffice | 19 (16) | 16/16 | 24.9 | 1.000 / 1.000 | 16/16 | VAV, CHW coil, HW reheat, chillers, boilers, pumps, cooling towers |
| RefBldgOutPatient | 118 (86) | 86/86 | 14.5 | 1.000 / 0.995 | 80/86 | VAV, DX coil, HW reheat, boiler, pumps |
| RefBldgMidriseApartment | 27 (27) | 27/27 | 7.4 | 1.000 / 1.000 | 27/27 | single-duct terminal, DX coil, heating coil |
| FanCoilAutoSize | 3 (3) | 3/3 | 15.0 | 1.000 / 1.000 | 3/3 | fan coil, CHW and HW coils, chiller, boiler, pumps |
| PackagedTerminalHeatPump | 3 (3) | 3/3 | 6.0 | 1.000 / 1.000 | 3/3 | PTHP, DX coil, heating coil |
| HVACTemplate-5ZonePackagedVAV | 6 (5) | 5/5 | 9.0 | 1.000 / 1.000 | 5/5 | VAV, DX coil, heating coil |
| HVACTemplate-5ZoneVAVWaterCooled | 6 (5) | 5/5 | 23.0 | 1.000 / 1.000 | 5/5 | VAV, CHW coil, HW reheat, chiller, boiler, pumps, cooling tower |
| HVACTemplate-5ZoneFanCoil | 6 (5) | 5/5 | 20.0 | 1.000 / 1.000 | 5/5 | fan coil, CHW and HW coils, chiller, boiler, pumps, cooling tower |
| HVACTemplate-5ZonePTAC | 6 (5) | 5/5 | 7.0 | 1.000 / 1.000 | 5/5 | PTAC, DX coil, heating coils, boiler |
Note: CHW, chilled water; HW, hot water; PTAC and PTHP, packaged terminal air conditioner and heat pump. Precision and recall are counted over components (@sec:s3-5).

## E5: provenance of image labels {#sec:s5-4}

The share of pixels whose temperature is simulated differs strongly between views and, for some views, in time (@fig:f8, mean over each camera's frames). Envelope views are mostly simulated: 99.9 % of the case B wall view and 83–95 % of the case C window view, whose remainder is ceiling under the jet of a diffuser near the façade. Plenum views are simulated or physics-derived while air and water flow: 81 % of case B's plenum view shows EnergyPlus surfaces and up to 19 % shows ducts and pipes whose temperatures follow from simulated fluid conditions through conduction; at night, when the fluid stagnates, these pixels become heuristic. Case C's plenum view is 98 % simulated. Ceiling views depend on the operating state: the jet field changes 76 % of case B's ceiling view by at least 0.1 K on the summer afternoon but only the diffuser and grille faces (5 %) at night, and 62–88 % of case C's diffuser view. Rooftop views rest on heuristic equipment and sol-air models, except where the model supplies the roof (57 % simulated in case C). The shares are weighted by pixel area and therefore depend on framing; small targets such as the valve or the void contribute few pixels, and their own levels are those of @tbl:t3.

![Share of image pixels by provenance of the displayed temperature, mean over each camera's rendered frames, for every camera of case studies B and C.](../docs/paper/figures/fig8_pixel_provenance.png){#fig:f8 width=6.5in}

## E6: sensitivity to non-simulated parameters {#sec:s5-5}

Because every element model has its own parameters, a non-simulated parameter can only move the contrasts of the elements it governs; this follows from the design and is not a finding. More informative is the ranking within each contrast (@tbl:t8). The envelope contrast (M1) depends, among the non-simulated parameters, only on the cavity resistance assumed for the void (μ* 1.1 K) and slightly on the camera setting. The duct-gap contrast (M2) is −1.5 K although the true contrast is 6.4 K, because both the bare galvanised segment and the foil-faced wrap are low-emissivity surfaces that largely reflect the plenum; the emissivity of the wrap's facing moves it by 1.5 K over its range (from −2.1 to −0.6 K as the emissivity rises from 0.1 to 0.6), about ten times the effect of the insulation conductivity or the film coefficient. The reheat valve after reheat stops (M3) is governed by the time constant of the water trapped in its body: over 15–60 min its contrast moves by 11.9 K and changes sign, from −1.4 K, a valve already colder than the pipe jacket, to +10.2 K. The diffuser and ceiling-jet contrasts (M4, M5) depend mainly on the diffuser effectiveness and the jet amplitude (μ* 3.0 and 2.2 K), and the rooftop contrasts (M6–M8) on the sol-air solar fraction (6.6–8.8 K), the motor rise (9.1 K), the condenser-coil fraction (3.4 K) and the casing coupling (3.1 K). The spreads σ are small against μ* for most parameters, so interactions are weak; the largest relative spreads are those of the pipe's time constant, the film coefficient and the insulation conductivity on M3, which act together on the trapped water. The heuristics that dominate the rooftop and ceiling views are thus also those whose parameters move the contrasts most, and the stagnation time constants decide what a camera sees in the hour after a valve closes. The camera's emissivity setting moves every contrast by 0.1–1.1 K and its reflected-temperature setting by at most 0.1 K, and every default contrast is about thirty times the camera's NETD or more. The ranges are illustrative bounds around the defaults, not calibrated uncertainties; the one-at-a-time results, which show the direction of each effect, are in the Supplementary Material.

Table: Morris screening of eight apparent contrasts of case B (camera set to ε = 0.95 and 20 °C reflected temperature): μ* (σ), the mean absolute and the standard deviation of the elementary effects, in K over each parameter's full range; ten trajectories on a four-level grid; — = μ* below 0.05 K. Contrasts: M1 wall defect − wall, winter 07:00; M2 duct gap − wrapped duct, summer 15:00; M3 reheat valve − pipe jacket, winter 10:00; M4 diffuser face − ceiling, summer 15:00; M5 ceiling 0.3 m beyond the diffuser edge − ceiling, summer 15:00; M6 pump motor − roof deck, summer 09:00; M7 condenser coil − roof deck, summer 15:00; M8 AHU fan section − roof deck, summer 15:00. {#tbl:t8}

| Parameter (range) | M1 | M2 | M3 | M4 | M5 | M6 | M7 | M8 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **Default contrast (K)** | **−2.7** | **−1.5** | **+3.3** | **−10.2** | **−3.0** | **+9.8** | **−13.6** | **−8.3** |
| Insulation conductivity (0.030–0.050 W/(m·K)) | — | 0.16 (0.12) | 1.35 (0.26) | — | — | — | — | — |
| Plenum film coefficient (6–10 W/m²K) | — | 0.12 (0.14) | 1.45 (0.45) | — | — | — | — | — |
| Stagnant-water time constant, insulated pipe (60–240 min) | — | — | 1.52 (0.76) | — | — | — | — | — |
| Stagnant-water time constant, bare valve (15–60 min) | — | — | 11.87 (1.49) | — | — | — | — | — |
| Envelope cavity resistance (0.10–0.30 m²K/W) | 1.06 (0.11) | — | — | — | — | — | — | — |
| Diffuser effectiveness $\eta_{\max}$ (0.70–0.95) | — | — | — | 2.97 (0.09) | 0.96 (0.23) | — | — | — |
| Ceiling-jet amplitude $a_{\max}$ (0.3–0.7) | — | — | — | — | 2.20 (0.35) | — | — | — |
| Ceiling-jet decay length $L_0$ (0.10–0.30 m) | — | — | — | — | 0.51 (0.14) | — | — | — |
| Duct-wrap facing emissivity (0.10–0.60) | — | 1.50 (0.16) | — | — | — | — | — | — |
| Motor temperature rise (10–20 K) | — | — | — | — | — | 9.08 (0.27) | — | — |
| Condenser-coil fraction $\phi_c$ (0.4–0.8) | — | — | — | — | — | — | 3.37 (0.09) | — |
| AHU casing coupling (0.02–0.10) | — | — | — | — | — | — | — | 3.13 (0.23) |
| Sol-air solar fraction $f_s$ (0.4–0.8) | — | — | — | — | — | 6.60 (0.19) | — | 8.76 (0.47) |
| Camera emissivity setting (0.90–1.00) | 0.25 (0.04) | 0.14 (0.07) | 0.39 (0.34) | 0.95 (0.13) | 0.28 (0.10) | 0.67 (0.36) | 1.06 (0.09) | 0.75 (0.21) |
| Camera reflected-temperature setting (10–30 °C) | — | — | — | 0.08 (0.07) | — | — | 0.07 (0.06) | — |

## Verification and cost {#sec:s5-6}

**Element models.** Against an independent steady calculation with natural convection from horizontal cylinders [@churchill1975horizontal] and linearised radiation in place of the fixed film coefficients, the surface temperatures of the physics-level models agree within 0.5 K for the insulated hot-water pipe (water at 30–80 °C), within 0.9 K for the wrapped supply duct and within 1.1 K for the bare duct segment (air at 12–35 °C), in plenum air at 24 °C. The bare valve body, whose fixed 3 % film-resistance ratio is labelled heuristic, differs by up to 3.8 K, depending on whether the water flow is taken as laminar or turbulent. Lumped-capacitance estimates of the stagnation time constant give 48 min for the wrapped 200 mm duct, 128 min for the insulated DN15 pipe and 31 min for a bare DN15 pipe standing in for the valve body; the defaults are these values rounded, and larger pipes, such as the DN50 chilled-water pipes on the roof, would relax more slowly. The details are in the Supplementary Material.

**Envelope defect.** A steady two-dimensional conduction model of a section through the case B wall (stucco, insulation and gypsum, with the same 0.17 m²K/W cavity in place of the insulation over 0.8 m) reproduces the one-dimensional contrast at the centre of the void to within 0.01 K and smears its edge over about 6 cm, from 90 % of the contrast 2 cm inside the edge to 10 % 5 cm outside it. EnergyPlus's one-dimensional treatment of the void is therefore adequate for this defect size; lateral conduction would matter for narrow defects such as cracks or studs.

**Renderers.** For every camera of every case (last preview frame), feeding the reference renderer's segmentation and depth through the shading path of the Isaac Sim script reproduces the reference images to better than 10⁻⁴ K. This is a self-consistency check of the shared shading code, not of GPU rendering. The browser viewer's lookup tables agree with the reference radiometry to within 10⁻³ K (10⁻⁴ K between 10 and 70 °C). Automated checks at every time step of every case find no missing temperature and no violation of three physical bounds: duct and pipe surfaces in a plenum lie between their fluid and the plenum air, the insulation gap is always closer to the fluid temperature than the wrapped duct, and diffuser faces lie between the ceiling and supply-air temperatures.

**Cost.** With the EnergyPlus run cached, building case B takes 11 s and case C 21 s; on the CPU, the one-off ray cast of a 320 × 240 camera takes 1.2–5.5 s and every further frame 0.02–0.14 s, so a year of 15-minute frames for one camera costs 11–82 minutes. Time-sampled storage suits periods of days (case B: 6.7 MB for two days); the sidecar format reduced case C from 48 MB to 0.48 MB.

## E7: visibility and energy relevance of the faults {#sec:s5-7}

The two faults of case B, and the void of case C, differ in how visible they are and in what they do to the building's heat flows (@tbl:t9).

**Envelope void.** On the two simulated days the void conducts 4.3 (winter) to 4.8 (summer) times as much heat per square metre as the insulated wall. Although it covers only 0.64 m² of the 12.4 m² opaque south wall, it raises the wall's daily conduction loss by 17 % in winter and its gain by 19 % in summer. Over the TMY3 year it adds 85 kWh (21 %) to the wall's conduction gains and 28 kWh (18 %) to its losses. A camera with default settings reads at least 1 K of contrast across the void during 75 % of all time steps of the year and 90 % of the occupied ones, but the sign changes within the day: on 306 of 365 days the void appears both warmer and colder than the wall by at least 0.5 K, because the sun-heated stucco conducts inward through the void in the afternoon. In case C (Chicago) the 0.8 m² void sits in a 71.6 m² wall: it changes the wall's daily conduction by 3–4 % and, over the year, adds 66 kWh (2.4 %) to the wall's conduction losses and 15 kWh (1.5 %) to its gains. Its contrast reaches 1 K in 82 % of all time steps and also changes sign on most days (309 of 365), but in this heating-dominated climate the void reads cold most of the time (median −1.6 K).

**Duct insulation gap.** While air flows, the unwrapped segment reads at least 1 K colder than the wrapped duct in 97 % of the time steps of the year (median −1.7 K), more consistently than the void. Yet the heat it adds to the supply air, beyond what the wrapped segment would admit, is small: 5.8 W on the summer afternoon and 30 kWh over the year, 1.0 % of the 3,054 kWh of sensible cooling delivered to the office. This is also the largest heat flow that the generated elements exchange outside EnergyPlus (@sec:s3-7), which justifies the one-way coupling for this case.

The more visible fault is thus the less important one for energy use, and the void's visibility depends on the time of day while its energy penalty accrues all year. A diagnosis method trained on these scenes can be given both labels, the signature and its energy consequence, which measured thermograms do not provide.

Table: Visibility and energy consequence of the injected faults of cases B (Phoenix) and C (Chicago) over the TMY3 year (camera set to ε = 0.95 and 20 °C reflected temperature; per zone instance). {#tbl:t9}

| Fault | Apparent contrast, median (5th–95th percentile) | Time steps with contrast ≥ 1 K | Energy consequence |
| --- | --- | --- | --- |
| Envelope void, B, 0.64 m² | +0.3 K (−2.8 to +6.3 K) | 75 % of all, 90 % of occupied; sign changes on 306 of 365 days | south-wall conduction +21 % (gains), +18 % (losses); +85 / +28 kWh |
| Envelope void, C, 0.8 m² | −1.6 K (−4.6 to +4.4 K) | 82 % of all, 78 % of occupied; sign changes on 309 of 365 days | south-wall conduction +1.5 % (gains), +2.4 % (losses); +15 / +66 kWh |
| Duct insulation gap, B, 0.18 m | −1.7 K (−2.4 to −1.3 K), while air flows | 97 % of steps with flow | +30 kWh to the supply air, 1.0 % of the office's sensible cooling |

# Discussion {#sec:s6}

## Implications {#sec:s6-1}

**Energy models as a source of labelled thermography.** Energy models already encode most of what a thermal camera sees in a building: surface temperatures, supply and return conditions, plant operation and their response to weather and schedules. The results show that this information carries through to images with the dynamics that make thermographic interpretation hard: contrasts that reverse within a day (@sec:s5-1, @sec:s5-7), equipment whose appearance depends on its control state, and operating states that follow from the model's sizing. Synthetic thermography generated this way varies with operation, weather and time of day, which randomising textures and lighting does not provide; whether this variation is what diagnosis methods need is a hypothesis for future benchmark tasks. Every image comes with labels that field data lack: the true temperature, the provenance of each pixel and the energy consequence of each fault.

**Visibility is not energy significance.** The duct-insulation gap is visible to a default camera almost whenever air flows, but adds about 1 % to the office's cooling; the envelope void is invisible or reversed at some hours, but adds a fifth to its wall's conduction gains and losses (@sec:s5-7). Automated thermographic diagnosis that ranks anomalies by contrast alone would therefore rank them wrongly for energy purposes. Coupling each signature to its simulated energy consequence, as done here, is a route to diagnosis methods that report what a fault costs, not only where it is.

**Names are not needed.** The topology tracer recovered the declared serving chain of 174 of 180 conditioned zones exactly, in twelve models written by different authors with different conventions, and found no undeclared component; the full pipeline ran on every zone of an unmodified reference building. Three design choices made this possible without per-model configuration: reading epJSON field names instead of positions, deriving ports and directions from field semantics, and treating loops and connectors as implicit links. The same choices let the pipeline follow systems nested in container objects and loops that pass through condenser circuits. Unlike the branch-node report that EnergyPlus writes for each loop [@energyplus2022bnd], the tracer returns the equipment that serves a given zone across air, plant and condenser loops, which is what a scene needs. The tested models cover central and packaged VAV, fan coils and packaged terminal units; variable-refrigerant-flow, radiant, dual-duct and ground-source systems remain to be tested before the tracer is applied to building-stock collections.

**Provenance and sensitivity turn assumptions into a calibration plan.** Per-frame provenance states how much of each image rests on simulation at each instant, and the sensitivity screening states which assumptions move the contrasts most. Together they rank what to calibrate first against field thermography: the sol-air view factors and casing couplings of rooftop equipment, the ceiling-jet amplitude, the motor and condenser heuristics, the cooling of water trapped in closed valves, and the emissivity of insulation facings, which governs the apparent contrast of the duct fault more than any thermal parameter. For users training diagnosis methods, provenance also allows labels to be weighted or filtered by confidence.

**One scene, several communities.** The output is a standard OpenUSD stage, which robot and vision tools read, and an RDF graph aligned with Brick and ASHRAE 223P, which building-metadata tools read. The same scene can serve a building scientist checking where a model's assumptions dominate, a developer of thermographic diagnosis methods, and a robotics researcher preparing inspection tasks.

## Limitations {#sec:s6-2}

- **No validation against measured thermograms.** EnergyPlus surface temperatures rest on a widely used engine, and the physics-level element models agree with standard correlations within about 1 K, but no image has been compared with field thermography, and the heuristic models (casings, compressors, motors, condensers, diffuser faces, ceiling jets, stagnant fluid) are uncalibrated. Their influence is quantified (@tbl:t8), not their accuracy.
- **Spatial resolution of simulated temperatures.** Each EnergyPlus surface has one temperature, so thermal bridges, corners and sunlit patches appear only when modelled as sub-surfaces. One-dimensional defects are adequate for defects much larger than the wall thickness (@sec:s5-6), not for cracks or studs. Generated elements use steady relations around simulated fluid temperatures and a first-order relaxation for stagnant fluid, not transient conduction, and the ceiling-jet overlay is not renormalised, so the mean of a ceiling image can depart from the EnergyPlus ceiling temperature near diffusers.
- **Radiometric and sensor simplifications.** Reflections use one reflected temperature per space (mean radiant temperature indoors, the mean of sky and air outdoors) instead of directional reflections, which matters most for low-emissivity metals and glass, and outdoor sol-air relations use fixed view factors and film coefficients rather than orientation and wind. The camera model has white noise and blur but no absolute calibration error, fixed-pattern noise or angle-dependent emissivity. Moisture is not modelled: the dew point is read, but surfaces below it are not shown wet.
- **Shortcuts for learned detectors.** Surfaces are spatially uniform apart from the modelled defects and jets, and the noise is white, so a detector trained on these images could exploit regularities that field thermograms lack; randomised emissivity textures, framing members and sensor non-uniformity would be needed before such training.
- **One-way coupling.** Heat exchanged by generated elements is not returned to EnergyPlus; for the duct gap of case B it is 1 % of the zone's cooling (@sec:s5-7), but larger element faults would need co-simulation.
- **Layout is a rule set, not an inference.** Ducts run orthogonally in one plenum without clash avoidance; mains do not continue across several modelled zones; rooftop units are placed in a row. Generated layouts are plausible, not observed, and are labelled as assumed layout. Where an energy model defines one terminal unit for a 983 m² zone, the scene shows one terminal box serving 40 diffusers, faithful to the model but not to typical design practice.
- **Cases and climates.** The thermal results come from two buildings in two climates and, except for the one-year analyses, from two days; case B's zone multiplier makes forty offices peak together and its plant reaches its capacity limit on the hottest afternoon. The scene reproduces whatever the model does, including such sizing artefacts.
- **Scene content and simulators.** Furniture, occupants, plug loads, lighting, electrical systems and visual appearance are not generated. The Isaac Sim script shares the tested shading path but has not been executed on GPU hardware.

## Future work {#sec:s6-3}

Four directions follow. First, validation and calibration with field thermography of plenums, ceilings and roofs, in the order suggested by the provenance and sensitivity results, together with measured emissivities of common facings. Second, scale: multi-zone scenes with shared mains and routing that avoids structure, applied to building-stock collections such as Model America [@new2021modelamerica], with fault libraries assembled from EnergyPlus fault models [@zhang2017modeling] and generated element faults to produce paired normal and faulty data with their energy penalties. Third, use: benchmark tasks for thermographic diagnosis (anomaly detection, fault localisation and ranking by energy consequence), trained on synthetic scenes and tested on measured datasets [@mayer2023thermal]. Fourth, closed-loop simulation: thermal cameras on moving robots and drones, with simulation time interpolated to the observer, and two-way coupling through the EnergyPlus runtime interface or a learned surrogate so that actions such as closing a valve change the thermal state.

# Conclusions {#sec:s7}

This paper presented thermoscene, a pipeline that turns an existing EnergyPlus model into a three-dimensional thermal scene of a zone, its envelope and the HVAC system that serves it, with simulated thermal images whose every pixel carries a true temperature, a camera reading and a provenance level. Its main findings are:

1. A name-free tracer recovers the equipment serving a zone from the node graph of an EnergyPlus model; on 180 conditioned zones of twelve example models covering eight HVAC system types it found no undeclared component and 99.8 % of the declared ones.
2. Elements that energy models lack (diffusers, ducts, pipes, valves, terminal boxes, air-handling units, chillers and their condensers) can be generated with standard sizing rules from autosized values and bound to simulated fluid conditions; the physics-level element models agree with standard heat-transfer correlations within about 1 K.
3. The pipeline runs unchanged on an unmodified DOE reference building and on all 15 of its conditioned zones, in 2–7 s per scene once the building is simulated.
4. Per-frame provenance shows where images rest on simulation and where on assumptions: envelope and plenum views are mostly simulated or physics-derived, while ceiling views depend on the heuristic jet model when the system runs and rooftop views on equipment heuristics.
5. Among the non-simulated parameters, rooftop view factors, the ceiling-jet amplitude, equipment heuristics and the cooling of water trapped in a closed valve move the apparent contrasts most, and the apparent contrast of a duct-insulation fault depends more on the emissivity of the insulation facing than on any thermal parameter.
6. How visible a fault is does not indicate its energy significance: over a year, a duct-insulation gap was visible almost whenever air flowed but added 1 % to the zone's cooling, whereas an envelope void raised its wall's conduction by about a fifth while its contrast reversed on most days.

By making building energy simulation a source of physically grounded, labelled thermography, the work links building performance simulation with automated thermographic diagnosis, whether by inspectors, drones or robots, and lets a diagnosis be tied to its energy consequence.

# Code and data availability {-}

The source code, the ontology specification, the configuration reference, the case-study inputs and the scripts that reproduce every table, figure and number of this paper (topology survey, zone sweep, sensitivity and Morris screening, annual fault analysis, benchmarks and figures) are available at https://github.com/TensorBuildLab/thermoscene under the MIT licence. Case study C uses the reference model and weather file distributed with EnergyPlus. A self-contained browser viewer exported from any scene lets readers move a thermal camera freely through case study B, scrub the simulated period and switch between camera readings, true temperatures and provenance.

# Acknowledgements {-}

To be completed by the authors.

# Declaration of generative AI and AI-assisted technologies in the manuscript preparation process {-}

During the preparation of this work the authors used Claude (Anthropic) to assist with software development, data analysis scripts, figure scripts and the drafting and editing of the text. After using this tool, the authors reviewed and edited the content as needed and take full responsibility for the content of the publication.

# References {-}

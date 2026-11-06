[Date]

The Editors-in-Chief
*Energy and Buildings*

Dear Editors,

We submit the manuscript **"From energy models to thermal scenes: an ontology-driven pipeline for physically grounded thermography of buildings and HVAC systems"** for consideration as a full-length research article in *Energy and Buildings*.

**Link to energy use in buildings.** Infrared thermography is the standard tool for finding the envelope and HVAC faults that waste energy, and its automation (by inspectors, drones and robots) is limited by the lack of labelled data that connect what a camera sees to how the building operates. The manuscript turns an existing EnergyPlus model into a three-dimensional thermal scene of a zone, its envelope and the HVAC system serving it, with simulated thermal images whose every pixel carries a true temperature, a camera reading and the provenance of that temperature. Faults injected into the model or the generated equipment have signatures and energy consequences computed by the same simulation. Over a year, an insulation void that raised its wall's conduction gains and losses by about a fifth was visible only at some hours and reversed its contrast on most days, whereas a duct-insulation gap that was visible almost whenever air flowed added about 1 % to the zone's cooling. Visibility therefore does not rank energy significance, which matters for any automated diagnosis that prioritises anomalies.

**Contributions.**
- A name-free HVAC topology tracer for EnergyPlus models; against the system structure declared in twelve example models (180 zones) it reached a precision of 1.000 and a recall of 0.998.
- Generation of ducts, pipes, valves, terminal boxes, air-handling units and chillers from EnergyPlus autosizing, bound to simulated nodes; the physics-based element models agree with standard heat-transfer correlations within about 1 K.
- Per-pixel, per-frame provenance of every image, and a global (Morris) sensitivity screening that together rank the assumptions to calibrate first against field thermography.
- Scenes for every zone of an unmodified U.S. DOE reference building, and open code that reproduces every table, figure and number.

The work is not validated against field thermography yet; the manuscript states this plainly and separates what rests on EnergyPlus output from what rests on heuristics, pixel by pixel.

The manuscript is original, has not been published, and is not under consideration elsewhere. [Preprint: state whether a preprint has been posted.] All authors have approved the submission. [Competing interests: Yiyuan Jia is affiliated with Big Ladder Software, a building energy simulation software company; state whether the company has any interest in this work.] [Funding: state funders and grant numbers, or that the work received no specific grant.] Code and data are publicly available at https://github.com/TensorBuildLab/thermoscene. The use of generative AI in preparing the manuscript is declared before the reference list.

[Suggested reviewers, if requested.]

Sincerely,

Liang Zhang, on behalf of all authors
University of Arizona, Tucson, AZ, USA
liangzhang1@arizona.edu

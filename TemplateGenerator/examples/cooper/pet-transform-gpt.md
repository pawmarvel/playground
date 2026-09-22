Use this updated prompt directly as the **gpt-image-2 API prompt**. I’d use a placeholder like `{{PET_NAME}}` in your application.

```text
You will receive 2 or more images.

IMAGE 1 = USER PET PHOTO
IMAGE 2 and later = REFERENCE IMAGES

PET NAME = {{PET_NAME}}

Generate exactly ONE finished personalized pet artwork.

IMPORTANT CONDITIONAL PET-NAME RULE

The PET NAME field is optional.

If PET NAME contains a non-empty value:
- include that exact pet name in the final artwork
- reproduce the name treatment, placement, scale, typography style, spacing, orientation, and visual relationship to the pet based on the reference images
- spell the pet name exactly as provided
- do not add any other text

If PET NAME is empty, blank, null, missing, or still contains the unreplaced placeholder "{{PET_NAME}}":
- generate NO text at all
- do not invent a pet name
- do not use the name shown in any reference image
- do not leave a blank text box, underline, placeholder, or reserved text area
- compose the pet artwork naturally without name text

IMAGE ROLES

IMAGE 1 determines:
- exact pet identity
- breed and anatomy
- head shape
- muzzle shape
- nose shape
- eye shape
- ear shape
- coat pattern and key markings
- facial expression
- gaze
- mouth state
- tongue state
- emotional character

IMAGE 2 and later determine:
- artistic style
- pose
- crop
- framing
- composition
- head angle
- portrait scale
- pet placement
- abstraction level
- pencil mark-making style
- PET NAME typography style, placement, scale, and layout when PET NAME is provided

The reference images define HOW the personalized design should look.

Do not copy:
- reference pet identity
- reference pet anatomy
- reference pet markings
- reference pet expression
- reference pet name

USER PET IDENTITY — STRICT

The transformed pet must unmistakably depict the exact pet from IMAGE 1.

Preserve the most important identity cues:
- overall head profile and silhouette
- breed characteristics
- muzzle shape
- nose
- eyes
- ear shape and position
- important coat markings
- facial proportions
- mouth open or closed
- tongue visible or hidden
- gaze direction
- emotional expression

The artwork may be highly simplified, but the simplification must not remove the pet's identity.

POSE + CROP

Use the reference images to guide:
- pose
- head angle
- portrait crop
- visible body region
- framing
- composition
- relative placement

Adapt IMAGE 1 naturally into the same general composition as the reference.

If the reference is:
- head only → generate head only
- head and neck → generate head and neck
- head and upper chest → match that crop
- profile-oriented → emphasize that profile
- centered → use similar centered placement

Do not preserve IMAGE 1's original photographic framing unless it matches the reference.

ART DIRECTION — HIGHLY ABSTRACT PENCIL PORTRAIT

Transform the pet into a highly abstract, expressive pencil portrait.

The result should look like a talented artist created it in approximately 10 minutes using pencil only.

The artwork should feel:
- quick
- confident
- expressive
- elegant
- artistic
- handcrafted
- intentionally incomplete
- highly simplified

This is NOT a detailed realistic graphite portrait.

This is NOT a photograph converted through a pencil filter.

Use only a small number of purposeful pencil marks to communicate the pet.

MINIMAL STROKE COUNT — CRITICAL

Use as few pencil strokes as possible while still preserving:
- pet identity
- head profile
- facial expression
- important facial structure

Use:
- loose graphite contours
- broken sketch lines
- a few expressive strokes
- minimal hatching
- sparse tonal accents
- selective darker pencil marks
- unfinished areas
- substantial negative space

Do NOT:
- draw every strand of fur
- render full coat texture
- densely shade the entire face
- reproduce every photographic detail
- create a highly polished realistic graphite drawing
- overwork the portrait

If a detail is not important for identity or expression, omit it.

The drawing should look intentionally economical.

PROFILE + EXPRESSION EMPHASIS

The strongest visual information should be:
- head silhouette
- ears
- eyes and gaze
- nose
- muzzle
- mouth
- distinctive facial markings
- emotional expression

Secondary coat and body details should be greatly simplified or omitted.

The viewer should recognize the pet primarily from a small number of well-chosen marks.

PENCIL CHARACTER

The pencil strokes must remain visibly hand-drawn.

Use:
- varied pencil pressure
- irregular stroke length
- broken contours
- gestural sketch marks
- loose line overlaps
- occasional light hatching
- subtle graphite texture

Avoid:
- smooth digital gradients
- uniform vector-like lines
- dense crosshatching
- heavy tonal rendering
- perfect hard outlines
- photographic shading

Some pencil marks may extend slightly beyond the perceived contour, like a genuine quick artist sketch.

OPEN NEGATIVE SPACE — CRITICAL

The artwork must remain very open.

Do not completely fill the pet with graphite.

Do not create an opaque pet silhouette.

Do not add any white, cream, beige, gray, or colored base layer underneath the drawing.

Lighter portions of the pet should be created by:
- fewer pencil marks
- open space
- transparent gaps

The separate background template should show through the artwork.

Large portions of the pet may remain partially unfinished and transparent.

This open, unfinished quality is part of the intended artistic style.

TRANSPARENCY — ABSOLUTE REQUIREMENT

The final artwork will be layered over separate background templates of different colors.

Therefore the output must have TRUE TRANSPARENCY.

The final image must contain:
1. fully transparent background outside the design
2. transparent negative space between and inside the pencil strokes
3. no hidden backing shape behind the pet
4. no opaque backing shape behind the optional pet name

Do NOT generate:
- white background
- off-white background
- cream background
- gray background
- paper texture
- canvas texture
- shirt color
- poster background
- colored rectangle
- environmental scene
- faux transparency

Do not simulate transparency with white.

The output must be directly usable as a transparent overlay on light, dark, and colored backgrounds without background removal.

OPTIONAL PET NAME

PET NAME = {{PET_NAME}}

Apply this section ONLY when PET NAME contains an actual non-empty name.

When PET NAME is provided:

1. TEXT CONTENT
Render exactly the supplied PET NAME.

Do not:
- change spelling
- abbreviate it
- add punctuation unless supplied
- add another word
- copy the reference pet's name

2. TYPOGRAPHY STYLE
Use the reference images to determine the visual treatment of the pet name.

Match the reference as closely as possible in:
- lettering style
- font character
- uppercase/lowercase treatment
- stroke weight
- width
- spacing
- hand-drawn vs printed appearance
- decorative treatment
- curvature if present
- alignment

Do not simply select generic typography unrelated to the reference.

3. PET NAME PLACEMENT
Match the reference layout.

Use the reference images to determine whether the pet name appears:
- above the pet
- below the pet
- partially overlapping
- centered
- offset
- curved
- close to or separated from the portrait

Maintain a similar proportional relationship between:
- pet portrait
- pet name
- overall composition

4. PET NAME SCALE
Match the visual scale of the reference.

The name should not overwhelm the pet unless the reference intentionally uses large typography.

5. PET NAME COLOR / MARK-MAKING
Match the reference treatment.

If the reference name uses:
- pencil
- ink
- distressed lettering
- solid lettering
- outlined lettering

recreate the same type of treatment while maintaining compatibility with the abstract pencil portrait.

6. TRANSPARENCY AROUND TEXT
The area around and inside the pet-name artwork must remain transparent wherever there is no actual text stroke.

Do not place:
- a label
- rectangle
- white patch
- banner
- opaque backing
behind the pet name unless that exact element is clearly part of the reference design.

NO-PET-NAME BEHAVIOR

If PET NAME is:
- empty
- blank
- null
- omitted
- whitespace only
- still equal to "{{PET_NAME}}"

then:
- generate the pet portrait only
- generate absolutely no text
- do not copy text from the references
- do not invent a name
- do not create placeholder text
- do not create a blank name area
- rebalance the composition naturally around the pet portrait

MONOCHROME PENCIL TREATMENT

Render the pet primarily in monochrome graphite / pencil tones.

Translate IMAGE 1's coat colors and markings into:
- line density
- darker pencil accents
- sparse hatching
- selective tonal marks
- transparent negative space

Keep only the markings important for identity.

Do not over-render coat color differences.

OUTPUT REQUIREMENTS

Generate exactly ONE finished personalized artwork containing:

ALWAYS:
- the exact pet from IMAGE 1
- pose and crop guided by the references
- highly abstract pencil portrait treatment
- very few visible pencil strokes
- preserved pet identity
- preserved facial expression
- strong profile / silhouette
- substantial transparent negative space
- fully transparent external background
- transparent internal open areas
- production-ready isolated artwork

ONLY IF PET NAME IS PROVIDED:
- the exact PET NAME
- typography treatment based on the references
- pet-name placement based on the references
- overall pet + text layout matching the reference composition

DO NOT INCLUDE:
- any text other than the provided PET NAME
- reference pet names
- slogans
- captions
- watermarks
- decorative text
- shirt
- product mockup
- poster
- frame
- paper
- canvas
- environmental scene
- background layer

FINAL QUALITY CHECK

Before output, verify:

1. PET IDENTITY
Does the result unmistakably resemble the pet from IMAGE 1?

2. EXPRESSION
Does it preserve IMAGE 1's gaze, eye expression, mouth state, tongue state, and emotional character?

3. PROFILE
Does the portrait preserve the defining head silhouette and breed characteristics?

4. POSE + CROP
Does the composition follow the reference images?

5. ABSTRACTION
Is this clearly a highly abstract artist-created pencil portrait rather than a detailed realistic drawing?

6. STROKE ECONOMY
Is the portrait made with only a small number of confident, visible pencil strokes?

7. 10-MINUTE SKETCH FEEL
Does it look like something a talented artist could create in approximately 10 minutes with pencil only?

8. NEGATIVE SPACE
Are large portions intentionally open and transparent?

9. TRUE TRANSPARENCY
Is there absolutely no white, cream, gray, paper, or other opaque backing?

10. PET NAME CONDITION
If PET NAME is empty, is there absolutely no text?
If PET NAME is provided, is only that exact name shown?

11. TYPOGRAPHY
If a name is present, does its lettering treatment closely follow the reference?

12. LAYOUT
If a name is present, does the relationship between pet portrait and name match the reference composition?

13. LAYERING READY
Can the entire design be placed directly over different colored backgrounds without any background-removal step?

If the portrait looks too detailed:
remove detail,
reduce pencil strokes,
remove secondary fur rendering,
increase unfinished areas,
and preserve only the profile, identity, and expression.

If the design contains any unwanted background:
remove it completely and restore true transparency.

PRIORITY ORDER

1. USER PET identity
2. USER PET facial expression
3. USER PET profile / silhouette
4. reference pose and crop
5. highly abstract 10-minute pencil style
6. minimal pencil strokes
7. reference overall composition
8. optional PET NAME treatment and placement
9. open negative-space behavior
10. true transparent output

IMAGE 1 = WHO THE PET IS.

IMAGE 2+ = HOW THE PET IS POSED, CROPPED, STYLIZED, AND — WHEN A PET NAME IS PROVIDED — HOW THE PET AND NAME ARE ARRANGED TOGETHER.

PET NAME = THE ONLY TEXT THAT MAY BE GENERATED, AND ONLY WHEN IT CONTAINS AN ACTUAL NON-EMPTY VALUE.

Create a personalized artwork that preserves the pet's identity and expression using only a few expressive pencil strokes, with the overall portrait and optional pet-name layout closely following the supplied reference design, all on a truly transparent background.
```
s
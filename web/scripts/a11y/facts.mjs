/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'facts', path: '/t/sgl/facts' },
]

export const fixtures = {
  "/api/facts": {
    "title": "Cat facts",
    "noun": "Cat fact",
    "icon": "pets",
    "another": "Meow another",
    "total": 240,
    "sections": {
      "Anatomy": [
        "Cats have 32 muscles in each ear and can rotate them 180 degrees.",
        "Cats cannot taste sweetness — they lack the gene for sweet receptors.",
        "A cat's nose print is as unique as a human fingerprint.",
        "Cats have a third eyelid (nictitating membrane) rarely visible in healthy cats.",
        "Cats have a free-floating clavicle, letting them squeeze through any gap their head fits through.",
        "A cat's heart beats 140–220 bpm — roughly twice as fast as a human's."
      ],
      "Senses": [
        "A cat's field of vision is about 200 degrees — wider than a human's 180.",
        "Cats need about one-sixth as much light as we do to see.",
        "The eyeshine is a mirror called the tapetum lucidum, bouncing light back through the retina.",
        "Cats are nearsighted: sharp up close, blurry past about 20 feet.",
        "Cats see blues and yellows well and reds poorly — red-green colour blindness, not black and white.",
        "Cat hearing reaches 64 kHz, nearly two octaves above the human ceiling and a full octave above a dog's."
      ],
      "Motion and balance": [
        "A cat can jump up to six times its own body length in a single leap.",
        "The righting reflex works from as little as a foot of fall and is fully developed by seven weeks.",
        "A falling cat rotates front half then back half, using its own inertia — no push-off required.",
        "A falling cat tops out near 60 mph, half a human's, and spreads out like a parachute.",
        "A 1987 study of 132 cats that fell from windows found those dropping past seven storeys were often less hurt.",
        "Domestic cats sprint at around 30 mph in short bursts."
      ],
      "Sleep and grooming": [
        "Cats sleep 12–16 hours a day, among the sleepiest mammals on Earth.",
        "A cat spends about two-thirds of its life asleep — a 12-year-old cat has been awake for four years.",
        "Cats are crepuscular, not nocturnal: dawn and dusk are the busy shifts.",
        "Cats dream: Michel Jouvet's 1960s experiments had sleeping cats acting out hunts.",
        "A cat nap really is short — bouts run 15 to 100 minutes.",
        "Cats spend up to half their waking hours grooming."
      ],
      "How cats talk": [
        "Cats can make around 100 distinct vocalizations; dogs manage about 10.",
        "Adult cats barely meow at each other. The meow is aimed at humans.",
        "Kittens meow at their mothers, then mostly stop — except with us.",
        "A cat's purr vibrates at 25–150 Hz, a frequency shown to aid bone healing.",
        "Purring isn't only contentment; injured and frightened cats purr too.",
        "The purr comes from laryngeal muscles twitching 25 to 150 times a second."
      ],
      "Behaviour": [
        "Kneading — making biscuits — is nursing behaviour that never got switched off.",
        "Cats cover their waste to keep their scent off the radar of anything bigger.",
        "Cats bring prey home as provisioning behaviour: you are a very bad hunter who must be fed.",
        "The post-litter-box zoomies have a name in the literature: frenetic random activity periods.",
        "A 2014 study of shelter cats found a cardboard box measurably lowered their stress.",
        "A 2017 study found cats will sit in a square of tape on the floor — an outline is box enough."
      ],
      "Health and lifespan": [
        "The world's oldest cat, Creme Puff, lived to be 38 years and 3 days old.",
        "Indoor cats typically live 12 to 18 years; outdoor cats average far less.",
        "Cats are obligate carnivores — without taurine in the diet, the heart and retinas fail.",
        "Paracetamol and ibuprofen are poisonous to cats; cats lack the liver enzyme to process them.",
        "Every part of a true lily is lethal to cats, pollen included.",
        "Onions, garlic, and chocolate are all toxic to cats."
      ],
      "Ailments, and the good news": [
        "Feline diabetes often goes into remission — a low-carb diet gets plenty of cats off insulin entirely.",
        "Hyperthyroidism has an actual cure: one dose of radioactive iodine fixes around 95% of cats, once.",
        "Chronic kidney disease is manageable rather than terminal — caught early, cats live years on diet and fluids.",
        "GS-441524 turned FIP from a death sentence into a course of treatment most cats walk away from.",
        "Cats with FIV usually live a normal lifespan; it spreads through deep bite wounds, so a settled household stays safe.",
        "Ringworm sounds dreadful and mostly clears on its own; treatment just shortens the wait."
      ],
      "Coats and genetics": [
        "Almost every calico and tortoiseshell cat is female — the orange gene rides on the X chromosome.",
        "Male calicos are XXY, about one in 3,000, and nearly always sterile.",
        "Roughly 80% of orange cats are male.",
        "The gene behind orange fur, ARHGAP36, was finally pinned down in 2025.",
        "Tortoiseshell patterning is X-inactivation made visible — a mosaic of two cell lineages.",
        "Siamese points are temperature-sensitive albinism: pigment only forms where the body runs cool."
      ],
      "Breeds": [
        "The CFA recognises about 45 breeds; TICA recognises more than 70.",
        "Most cats on Earth belong to no breed at all.",
        "The Maine Coon is the largest domestic breed — the record is just under four feet nose to tail.",
        "The Singapura is the smallest breed, often under five pounds.",
        "The Sphynx isn't hairless; it's covered in fine down and needs regular baths.",
        "Manx taillessness comes from a mutation that can also damage the spine."
      ],
      "Wild relatives": [
        "There are around 40 species in the cat family.",
        "The domestic cat, Felis catus, descends from the African wildcat, Felis lybica.",
        "A 2007 study traced every domestic cat to five maternal lineages from the Fertile Crescent.",
        "The Scottish wildcat is critically endangered — mostly by breeding with house cats.",
        "The black-footed cat is the deadliest cat alive: about 60% of its hunts succeed.",
        "The rusty-spotted cat is the smallest wild cat, about the weight of a bag of sugar."
      ],
      "History": [
        "The oldest evidence of cats and people together is a 9,500-year-old joint burial in Cyprus.",
        "Cats domesticated themselves, more or less, by moving in on the rodents in Neolithic grain stores.",
        "Ancient Egyptians worshipped the cat goddess Bastet; harming a cat was punishable by death.",
        "Herodotus wrote that Egyptian families shaved their eyebrows in mourning when a house cat died.",
        "Egypt banned the export of cats, which did nothing to stop sailors taking them anyway.",
        "In 1888, a shipment of hundreds of thousands of Egyptian cat mummies was sold in England as fertiliser."
      ],
      "Myth and superstition": [
        "The nine lives are regional: much of the Arabic- and Turkish-speaking world gives cats six or seven.",
        "In Britain and Japan a black cat crossing your path is good luck. In much of the US it's the opposite.",
        "The maneki-neko's raised right paw invites money; the left invites customers.",
        "Freyja's chariot, in Norse myth, is pulled by cats.",
        "Sailors prized polydactyl cats, believing the extra toes helped them keep their footing at sea.",
        "The Cat Sìth of Highland folklore was a black cat with a white chest that could steal a soul."
      ],
      "Cats with jobs": [
        "Larry has been Chief Mouser to the Cabinet Office at 10 Downing Street since 2011.",
        "The Hermitage Museum in St Petersburg has kept cats on staff since a 1745 decree; about 50 live there now.",
        "New York's bodega cats are technically illegal and universally tolerated.",
        "Tama, a calico, was stationmaster of Kishi Station in Japan and is credited with saving the line.",
        "Stubbs the cat was honorary mayor of Talkeetna, Alaska, for 20 years.",
        "Simon of HMS Amethyst is the only cat to receive the Dickin Medal for gallantry."
      ],
      "Famous cats": [
        "Grumpy Cat's expression came from feline dwarfism and an underbite.",
        "Lil Bub was polydactyl, had osteopetrosis, and became a subject of published genetic research.",
        "Maru, the Japanese cat who cannot resist a box, has been viewed hundreds of millions of times.",
        "Orangey is the only cat to win two PATSY awards, including one for Breakfast at Tiffany's.",
        "Choupette, Karl Lagerfeld's Birman, had her own staff.",
        "Ernest Hemingway's Key West house still keeps about 60 cats, many of them polydactyl."
      ],
      "Records": [
        "A group of cats is called a clowder.",
        "The loudest recorded purr belongs to Merlin, at 67.8 decibels — about as loud as a shower.",
        "The longest jump by a cat is seven feet, set by Waffle the Warrior Cat.",
        "The most toes on a cat is 28.",
        "The longest fur on a cat measured just over nine inches.",
        "The smallest adult cat on record stood about seven centimetres at the shoulder."
      ],
      "Words for cats": [
        "A group of kittens is a kindle.",
        "A male cat is a tom; a female is a queen; a neutered male is a gib.",
        "'Cat' comes from Late Latin cattus, and nearly every European language kept the root.",
        "The ancient Egyptian word for cat was 'miu' — they named it after the noise.",
        "An ailurophile loves cats. An ailurophobe does not.",
        "Catgut was never made from cats. It's sheep intestine."
      ]
    }
  }
}

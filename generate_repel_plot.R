# generate_repel_plot.R
library(ggplot2)
library(ggrepel)

# Load the data exported from Python
stats <- read.csv("ward_stats_for_r.csv")

# Create the plot
p <- ggplot(stats, aes(x = White_Pct, y = Normalized_Sites, label = Display_Label)) +
    # Add the trendline
    geom_smooth(method = "lm", color = "gray60", linetype = "dashed", se = FALSE) +
    # Add the markers (matching your firebrick style)
    geom_point(size = 5, color = "white", fill = "firebrick", shape = 21, stroke = 1.5) +
    # THE REPEL MAGIC: Smart label placement
    geom_text_repel(
        size = 3,
        fontface = "bold",
        box.padding = 0.8, # Space around the text box
        point.padding = 0.5, # Space between point and label
        force = 2, # Strength of repulsion
        max.overlaps = Inf, # Ensure all labels are shown
        segment.color = "grey50", # Lines connecting label to point
        segment.alpha = 0.5,
        min.segment.length = 0
    ) +
    # Labels and Styling
    labs(
        title = "SUD Supportive Housing Density vs. Racial Demographics by Ward",
        subtitle = "Analysis of site concentration normalized by population",
        x = "Percentage White (non-Hispanic) Residents",
        y = "SUD Supportive Housing Sites per 10,000 Residents",
        caption = "Source: U.S. Census Bureau 2020 Redistricting Data; BPDA Research Division"
    ) +
    scale_x_continuous(labels = function(x) paste0(x, "%")) +
    theme_minimal(base_size = 16) +
    theme(
        plot.title = element_text(face = "bold", size = 20),
        panel.grid.major = element_line(color = "lightgray"),
        panel.grid.minor = element_blank(),
        plot.background = element_rect(fill = "white", color = NA),
        panel.background = element_rect(fill = "white", color = NA)
    )

# Save as a high-resolution PNG for your poster
ggsave("demographic_scatter_final.png", plot = p, width = 14, height = 9, dpi = 300)

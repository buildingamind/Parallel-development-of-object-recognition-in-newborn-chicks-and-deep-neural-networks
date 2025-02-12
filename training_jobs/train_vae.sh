viewpoint=V1O1

for viewpoint in train1_80k
do
    python3 ../train_vae.py \
        --max_epochs 100 \
        --batch_size 128 \
        --data_dir /data/lpandey/UT_Austin_EgocentricDataset/output_64x64Squished/${viewpoint} \
        --seed_val 0 \
        --dataset_size 80000 \
        --enc_type resnet18_2b \
        --shuffle \
        --val_split 0.1 \
        --exp_name test_runs_nov3/${viewpoint}
done

# NOTES :
# --shuffle \
# --print_model \
